# Metaproc Execution Model

The design decisions beneath task-level scheduling: what an edge means, when a roster is
complete, which attempt owns a result, and what a status file is allowed to claim.
These are the contracts that are expensive to change once process specs depend on them,
so they are settled here, deliberately, before the production scheduler grows into them.
[Process Framework Concepts](process-framework-concepts.md) supplies the vocabulary and
the design tests this instantiates;
[arch-execution-model.md](arch/arch-execution-model.md) covers the executable reference
model that implements these decisions in `src/metaproc/execution_model/`.

## Why an Execution-Model Revision

Metaproc executes a process by walking topological *levels* of the step graph: every
step in level *k* finishes before level *k+1* starts.
Within one fan-out step it does something better, running a streaming ready-set over
items. The gap between those two schedulers is where every fan-out workaround lives.
Closing it means making the task the scheduled unit, and that is not one change: it
requires durable, unambiguous answers to four questions, because an event-driven
scheduler loses the implicit guarantees the level walk provides today.

Eight decisions follow, each with its rationale.
Everything here is engine-internal unless a section says “authored”.
The authored surface grows by a handful of optional fields; the compiler lowers
shorthand into explicit persisted records.

## Process-Semantics Versioning

A spec declares the semantics version its edges are written against:

```yaml
process_semantics: metaproc/process/0.7
```

Absent the field, a spec means what it means today: `needs` is a step-scoped barrier.
This is the compatibility floor and it never expires.

The rule, from the concepts doc’s design test 16:

> A framework upgrade must never silently change what an existing spec’s edges mean.

Every semantic addition is gated on the declared version, the compiler records the
meaning it resolved, and resume executes the persisted resolution rather than whatever
the current compiler would infer later.
An audit-then-flip migration was considered and rejected: it makes correctness depend on
having audited every spec in every consuming repository, which is not a property the
framework can check.

## Task Identity

A task is addressed by:

```text
TaskKey = (run_id, scope_path, step_id, item_key?)
```

`scope_path` is the sequence of enclosing composite scopes, empty for a top-level step.
A child task inside a composite scope instantiated for item `k` of step `prelim` has
scope path `prelim[k]`, giving keys like `prelim[k]/fetch`. Hierarchy is what lets a
composite be a scope rather than a task holding a resource slot.

A key is meaningless without the identity domain it belongs to, so the framework records
a **key-space identifier** alongside every roster and enforces uniqueness within it.
Two rosters may both contain the string `ACME` while meaning different things: an entity
versus one of its listed identifiers, a current name versus a name as of a date, an
entity versus an entity-scenario pair whose display key was flattened.
Logical keys are structured data; the filesystem path is a rendering.

A task has a **desired generation**, a monotonically increasing integer.
Retry does not change it; a new attempt for the same generation is simply another try.
Force, or a changed upstream commit, increments it.
This is the distinction that makes invalidation precise: retrying a task must not
invalidate descendants that have not run yet, while forcing it must mark descendant
commits stale along exactly the dependency mappings that reach the changed generation.

## Dependency Clauses

An edge is a **dependency clause** with four independent axes:

```text
DependencyClause = (upstream, mapping, requirement, cardinality, binding)
```

1. **Mapping:** which upstream tasks relate to this downstream task: `same_key`,
   `broadcast`, `collect_all`, `group_by`, or an explicit relation artifact.
2. **Requirement:** which upstream outcomes satisfy the clause: `succeeded`, `finished`
   (terminal either way), `failed` for recovery paths, `always` for ordering-only edges.
3. **Cardinality:** `all`, `any`, or a threshold.
4. **Binding:** which committed outputs reach the consumer, and in what form: one
   artifact per aligned task, a typed collection of references with outcome descriptors,
   or an ordered group.

The first implementation exposes `same_key`, `broadcast`, and `collect_all`, with `all`
cardinality and `succeeded | finished` requirements.
The internal model keeps all four axes separate regardless, because collapsing them is
what forces the next migration.
`broadcast` requires a non-expanding upstream, or an expansion that closed with exactly
one key: there is no principled “the one” item of a fan-out.

The requirement names are `succeeded` and `finished` because each says exactly which
condition it means; “completed” reads as a terminal umbrella in some contexts and as
success in others, so it is avoided.
`finished` includes cancelled and dependency-skipped tasks, and every expected task in a
closed expansion contributes an outcome descriptor, so a consumer can tell a successful
artifact from a failure, a cancellation, and a skip.

Clauses derive from typed inputs rather than a second, richer `needs` language, which
would create two sources of truth:

```yaml
steps:
  - id: interpret
    for_each: {over: deps.roster, bind: item}
    inputs:
      measurement:
        from: measure.outputs.result
        map: same_key
        require: succeeded
```

`needs: [measure]` remains as sugar and lowers to an explicit clause when the mapping is
unambiguous. A control-only edge with no data binding uses `after:`. A fan-in consumer
receives a typed collection, and never rediscovers upstream state by walking
directories.

Two stability rules complete the model.
`same_key` may be inferred only from proven lineage: both steps expand from the same
expansion generation, or one roster declares itself derived from the other in the same
key space with subset membership validated.
Matching key strings across unrelated rosters is coincidence.
And operator flags never alter clause satisfaction: `--continue-on-error` governs
whether the scheduler abandons unrelated work and what verdict the run reports, never
whether a dependency is satisfied.

## Expansions and Closure

A roster-backed fan-out materializes as an **expansion generation** whose closure is a
durable fact: the generation identifier, the producing task’s commit, the roster
fingerprint, the key-space identifier, the key set, uniqueness validation, the
derived-from relation where one exists, and the closure stamp.

A fan-in clause is satisfiable only when the expansion is closed *and* the clause’s
outcome and cardinality condition holds over the closed set.
Without this, an event-driven scheduler has a live premature-barrier bug: at some
instant every visible task is terminal, the clause looks satisfied, and a still-running
producer then reveals more items.
Level-synchronous execution hides this today, because the producing step provably
finished before any consumer starts.
Closure makes that implicit guarantee explicit before the thing providing it is removed.

The empty case follows from the same rule: an empty *closed* roster satisfies `all`
vacuously and fails `any`; an absent or still-materializing roster satisfies neither.
Re-running a roster producer creates a new generation, never a mutation; tasks from the
old generation become historical.

## Attempts, Commits, and Fencing

Three distinct durable records:

```text
TaskRecord    = (task_key, desired_generation, current_attempt, terminal_outcome?)
AttemptRecord = (attempt_id, task_generation, fence_epoch, claim, placement, disposition)
CommitRecord  = (task_generation, attempt_id, validated_manifest, fingerprints, receipts)
```

Attempt history is append-only.
Outputs are staged attempt-privately and become visible only at commit.
At most one commit is accepted per task generation, covering the *complete* output set,
so a crash between two output files cannot leave a half-visible completion.
A commit carries provenance: upstream commit identifiers, input fingerprints, and the
code, configuration, and as-of parameters in force.

Fencing is required, not an optimization, because reclaiming a stale lease does not stop
the previous holder: a worker that was merely slow or partitioned can finish afterwards
and try to publish. A commit succeeds only if the attempt’s fence epoch is still current
and no commit exists for the generation.
A superseded attempt’s ending, *whatever its disposition*, is recorded as history and
nothing more.
On a filesystem this is a create-only commit record under a scheduler-owned
claim, or an atomic rename plus epoch check; on an object store, a generation
precondition. The storage primitive may vary; the semantic requirement does not.

## Admission

Readiness is a dependency fact: every clause is satisfied.
Admission is a resource fact: some authority granted capacity, here, now.
They are answered by different components and must not be conflated.

A request is a **claim**, vector-valued from the start even though the first
implementation honors only concurrency and estimated resident memory.
Each ceiling is scoped to the truth it tracks: host memory per host, shared across every
run on it; provider concurrency and rate per provider-account-model namespace;
politeness per declared resource key; step maxima per authored scope; run budgets as a
durable per-run ledger; operator caps globally and temporarily.

“One pool per run” is not the abstraction.
One *logical scheduler* per run derives readiness; admission authorities sit at their
own scopes; a placement backend chooses where an attempt runs; an executor supervises
it. RunPool remains the local executor and host-admission implementation.

A composite scope holds no slot: child tasks compile under the scope path and enter the
same scheduler and authorities, and only executable child attempts consume capacity.
The alternative, a parent task holding one slot while running a child scheduler,
deadlocks once the pool fills with parents.
Fairness is deterministic: retries first, aging, fair rotation across scopes, optional
per-step maxima; minimum-slot reservations are not offered until a workload demonstrates
starvation.

## The Resolved Plan

The compiler persists, and resume executes: the static template graph and every explicit
dependency clause, including what each piece of shorthand resolved to and why; each
step’s expansion contract; every expansion record materialized so far; and the semantics
version in force. This is what makes `metaproc plan` meaningful under dynamic width, and
it is what lets `metaproc status` say “waiting for roster `depth_roster` generation 2 to
close” instead of a generic pending state.

## Outcomes

A task’s terminal outcome is one of `succeeded | failed | cancelled | skipped(reason)`.
`partial` is **not** a task outcome: it is an aggregate over a step, scope, or run,
alongside `succeeded`, `failed`, `cancelled`, and `incomplete`. Dependency clauses
consume individual task outcomes while operators read aggregates, and conflating them
makes `partial` mean two different things at two altitudes.
Operational labels such as `ready` and `admission_wait` are projections derived from
durable facts, never stored as truth.

## Deliberately Left Open

- **Quota-namespace defaults.** How provider, account, and model or region compose into
  a default namespace key per adapter.
  Keyed only by execution profile is likely wrong in both directions: two profiles can
  share one account quota, one profile can span regional quotas.
- **Scale envelope, durable side.** The in-memory envelope is confirmed and guarded (see
  the [architecture doc](arch/arch-execution-model.md)); filesystem metadata load,
  per-task status writes, event-log volume, and resume time at the same envelope are
  unmeasured.
- **Threshold cardinality and `group_by`.** Modeled, not implemented, until a workload
  needs them.

## What This Design Does Not Include

Kept out on purpose, so the model stays small:

- arbitrary runtime creation of step types or edges;
- a general expression language for dependency predicates;
- a database-backed workflow service;
- speculative preemption or multidimensional bin packing;
- any domain primitive in the engine;
- minimum-slot reservations without demonstrated starvation;
- a second durable scheduler inside the launch library;
- opaque nested child runs with independent schedulers;
- cross-roster alignment by string coincidence;
- mutable roster generations;
- status projections treated as truth.

The model is small: templates, closed expansions, mapped dependency clauses, task
generations, attempts, commits, admission claims, and effects.
Everything else is projection or policy.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
