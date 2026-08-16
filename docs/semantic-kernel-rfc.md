# RFC: The Semantic Kernel

**Status:** Draft, open for review

**Supersedes:** the scheduling portions of
[metaproc-design-rev3-proposals.md](metaproc-design-rev3-proposals.md) P1 and P3

**Depends on:** [process-framework-concepts.md](process-framework-concepts.md), whose
vocabulary and sixteen design tests this RFC instantiates

## What this decides

Metaproc executes a process by walking topological *levels* of the step graph: every
step in level *k* finishes before level *k+1* starts.
Within one fan-out step it does something better, running a streaming ready-set over
items. The gap between those two schedulers is where every workaround lives.

Closing that gap is not one change.
Making the task the scheduled unit requires knowing, durably and unambiguously, what an
edge means, when a roster is complete, which attempt owns a result, and what a status
file is allowed to claim.
Those are semantic contracts, and they are expensive to change once specs depend on
them. This RFC settles them first, as a state table and a pure function, before any
production scheduler code moves.

Eight decisions, each a section below:

| § | Decision |
| --- | --- |
| 1 | Process-semantics versioning, so an upgrade cannot silently change what a spec means |
| 2 | Task identity: hierarchical keys, key spaces, generations |
| 3 | Dependency clauses: four independent axes, derived from typed inputs |
| 4 | Expansions: generations, closure, and derivation |
| 5 | Attempts, commits, and fencing |
| 6 | Admission claims, separate from readiness |
| 7 | The resolved plan as the persisted authority |
| 8 | Task outcomes, distinct from aggregate outcomes |

Sections 9 and 10 give the state tables and the reducer interface.
Section 11 lists the invariants the reference implementation must prove.
Section 12 records what this RFC deliberately leaves open.

Everything here is engine-internal unless a subsection says “authored”.
The authored surface grows by four optional fields; the compiler lowers shorthand into
the explicit records below, and the resolved plan persists them.

## 1. Process-semantics versioning

A spec declares the semantics version its edges are written against:

```yaml
process_semantics: metaproc/process/0.7
```

Absent the field, a spec means what it means today: `needs` is a step-scoped barrier.
This is the compatibility floor and it never expires.

The rule, from design test 16:

> A framework upgrade must never silently change what an existing spec’s edges mean.

So every semantic addition in this RFC is gated on the declared version, the compiler
records the meaning it resolved, and resume executes the persisted resolution rather
than whatever the current compiler would infer later.
An audit-then-flip migration was considered and rejected: it makes correctness depend on
having audited every spec in every consuming repository, which is not a property the
framework can check.

## 2. Task identity

### 2.1 Keys

A task is addressed by:

```text
TaskKey = (run_id, scope_path, step_id, item_key?)
```

`scope_path` is the sequence of enclosing composite scopes, empty for a top-level step.
A child task inside a composite scope instantiated for item `k` of step `prelim` has
scope path `prelim[k]`, giving keys like `prelim[k]/fetch`. Hierarchy is what lets a
composite be a scope rather than a task holding a resource slot (§6.3).

`item_key` is absent for a step with no roster.
Where a step deliberately runs the same item under several configurations, the variant
joins the identity rather than being encoded into the item key by string concatenation.

### 2.2 Key spaces

A key is meaningless without the identity domain it belongs to.
The framework records a **key-space identifier** alongside every roster and enforces
uniqueness within it.
Two rosters may both contain the string `ACME` while meaning different things: a company
versus a listed instrument, a current symbol versus a symbol as of a date, an entity
versus an entity-scenario pair whose display key was flattened.

Logical keys are structured data; the filesystem path may use a readable slug plus a
digest. The logical key is canonical, the path is a rendering.

### 2.3 Generations

A task has a **desired generation**, a monotonically increasing integer.
Retry does not change it; a new attempt for the same generation is simply another try.
Force, or a changed upstream commit, increments it.

This is the distinction that makes invalidation precise.
Retrying a task must not invalidate descendants that have not run yet, while forcing it
must mark descendant commits stale along exactly the dependency mappings that reach the
changed generation.

## 3. Dependency clauses

### 3.1 Four axes

An edge is a **dependency clause**:

```text
DependencyClause = (upstream, mapping, requirement, cardinality, binding)
```

1. **Mapping** — which upstream tasks relate to this downstream task: `same_key`,
   `broadcast`, `collect_all`, `group_by`, or an explicit relation artifact.
2. **Requirement** — which upstream outcomes satisfy the clause: `succeeded`, `finished`
   (terminal either way), `failed` for recovery paths, `always` for ordering-only edges.
3. **Cardinality** — `all`, `any`, or a threshold.
4. **Binding** — which committed outputs reach the consumer, and in what form: one
   artifact per aligned task, a typed collection of references with outcome descriptors,
   or an ordered group.

The first implementation exposes `same_key`, `broadcast`, and `collect_all`, with `all`
cardinality and `succeeded | finished` requirements.
`broadcast` requires a non-expanding upstream, or an expansion that closed with exactly
one key: there is no principled “the one” item of a fan-out, so a broadcast clause over
a wider roster is deterministically unsatisfiable rather than silently bound to the
first item. The internal model keeps all four axes separate regardless, because
collapsing them is what forces the next migration.

`all_completed` and `all_terminal` are rejected as names: “completed” reads as a
terminal umbrella in some places and as success in others.
`succeeded` and `finished` say which is meant.
`finished` includes cancelled and dependency-skipped tasks, and every expected task in a
closed expansion contributes an outcome descriptor, so a consumer can tell a successful
artifact from a failure, a cancellation, and a skip.

### 3.2 Clauses derive from typed inputs

Metaproc already has typed inputs and outputs.
A second, richer `needs` language would create two sources of truth, so clauses are
expressed as properties of an input:

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
unambiguous. A control-only edge with no data binding uses `after:`.

This follows the ownership principle from the concepts doc: the domain declares the
fields that govern routing, the framework owns the routing.
A fan-in consumer receives a typed collection, and never rediscovers upstream state by
walking directories.

### 3.3 Alignment is inferred only from proven lineage

`same_key` may be inferred when, and only when:

1. both steps expand from the same expansion generation; or
2. one roster declares itself derived from the other, preserving the key space, and
   subset membership validates.

Otherwise the mapping must be declared.
Matching key strings across unrelated rosters is coincidence, and a framework that
aligns on it joins unrelated work silently.

### 3.4 Operator flags never alter clause satisfaction

`--continue-on-error` governs whether the scheduler abandons unrelated work and what
verdict the run reports.
It does not decide whether a dependency is satisfied.
Otherwise one spec means two different graphs depending on how it was invoked.

## 4. Expansions

### 4.1 Lifecycle

```text
unmaterialized -> materializing -> closed
                          \-> failed
```

Closing an expansion atomically publishes: the generation identifier, the producing
task’s commit identifier, the roster artifact fingerprint, the key-space identifier and
schema, the key set, the uniqueness validation result, the derived-from relation where
one exists, the item count, and the closure stamp.

### 4.2 Closure is what makes fan-in safe

A fan-in clause is satisfiable only when the expansion is closed *and* the clause’s
outcome and cardinality condition holds over the closed set.

Without this, an event-driven scheduler has a live premature-barrier bug: at some
instant the scheduler sees eighty tasks, all eighty are terminal, the clause looks
satisfied, and a still-running producer then reveals twenty more.

Level-synchronous execution hides this today, because the producing step provably
finished before any consumer starts.
Closure is therefore not a new requirement so much as an existing implicit guarantee
being made explicit before the thing that provides it is removed.

The empty case follows from the same rule: an empty *closed* roster satisfies `all`
vacuously and fails `any`; an absent or still-materializing roster satisfies neither.

### 4.3 Regeneration creates a generation, never a mutation

Re-running a roster producer does not edit the item set in place.
It creates a new expansion generation.
Tasks from the old generation become historical; they are not silently reused under a
different universe. Resume then compares generation and commit identifiers instead of
guessing which files on disk are still valid.

## 5. Attempts, commits, and fencing

### 5.1 Three distinct records

```text
TaskRecord    = (task_key, desired_generation, current_attempt, terminal_outcome?)
AttemptRecord = (attempt_id, task_generation, fence_epoch, claim, placement, disposition)
CommitRecord  = (task_generation, attempt_id, validated_manifest, fingerprints, receipts)
```

Attempt history is append-only.
Outputs are staged attempt-privately and become visible only at commit.
At most one commit is accepted per task generation, and it covers the *complete* output
set, so a crash between two output files cannot leave a half-visible completion.

A commit carries provenance: the upstream commit identifiers it consumed, input
fingerprints, and the code, configuration, and as-of parameters in force.

### 5.2 Fencing is required, not an optimization

Reclaiming a stale lease does not stop the previous holder.
A worker that was merely slow or partitioned can finish afterwards and try to publish.

```text
1. scheduler grants attempt A2 with fence epoch 7
2. A2 stages output privately
3. commit succeeds only if epoch 7 is still current and no commit exists for the generation
4. a late attempt at epoch 6 is refused, even though its process exited zero
```

On a filesystem this is a create-only commit record under a scheduler-owned claim, or an
atomic rename plus epoch check.
On an object store it is a generation precondition.
The storage primitive may vary; the semantic requirement does not.

### 5.3 Retries belong to attempts, causal validity to generations

A retry is a new attempt at the same generation.
A force, or a changed input, is a new generation.
That split is what allows retries that do not disturb descendants, forces that correctly
mark descendants stale, and deterministic resume after a crash during retry backoff.

## 6. Admission

### 6.1 Readiness is not admission

Readiness is a dependency fact: every clause is satisfied.
Admission is a resource fact: some authority granted capacity, here, now.
They are answered by different components and must not be conflated.

### 6.2 Claims and authorities

A request is a **claim**, vector-valued from the start even though the first
implementation honors only concurrency and estimated resident memory:

```text
AdmissionRequest = (resource_claims, quota_namespaces, priority, deadline)
```

Each ceiling is scoped to the truth it tracks:

| Resource truth | Authority scope |
| --- | --- |
| Machine memory, local process count | Per host, shared across all runs on it |
| Provider concurrency | Per provider plus account plus model or region, as applicable |
| Provider rate | Token or request bucket in the same quota namespace |
| Politeness | Per declared domain or resource key |
| Step maximum | Per step or scope, authored |
| Run budget | Per run, durable reservation ledger |
| Operator cap | Global or selected scope, temporary and observable |

“One pool per run” is not the abstraction.
One *logical scheduler* per run derives readiness; admission authorities sit at their
own scopes; a placement backend chooses where an attempt runs; an executor supervises
it. RunPool remains the local executor and host-admission implementation.

### 6.3 A composite scope holds no slot

A composite step with a roster creates a resource-neutral **scope** per item.
Child tasks compile under that scope path and enter the same scheduler and the same
authorities. The scope itself consumes nothing; only executable child attempts do.

The alternative, a parent task holding one slot while running a child scheduler,
deadlocks: fill the pool with parents and no capacity remains for the children they are
waiting on. Measuring the process tree’s memory does not fix this, because tree
measurement is reactive pressure observation, not an admission protocol, and it cannot
represent provider quotas at all.

### 6.4 Fairness is deterministic

Retries first, then aging so waiting tasks gain priority, then fair rotation across
scopes, with optional per-step maxima.
Minimum-slot reservations are not offered until a workload demonstrates starvation,
because they waste capacity and can deadlock.

## 7. The resolved plan

The compiler persists, and resume executes:

- the static template graph and every explicit dependency clause, including what each
  piece of shorthand resolved to and why;
- each step’s expansion contract;
- every expansion record materialized so far;
- the semantics version in force.

This is what makes `metaproc plan` meaningful under dynamic width: it can show known
widths and pending ones, and `metaproc status` can say “waiting for roster
`depth_roster` generation 2 to close” instead of a generic pending state.

## 8. Outcomes

A task’s terminal outcome is one of:

```text
succeeded | failed | cancelled | skipped(reason)
```

`partial` is **not** a task outcome.
It is an aggregate over a step, scope, or run, alongside `succeeded`, `failed`,
`cancelled`, and `incomplete`.

The separation matters because dependency clauses consume individual task outcomes while
operators read aggregates, and conflating them makes `partial` mean two different things
at two altitudes.

Operational labels such as `ready`, `admission_wait`, `retry_wait`, and `running` are
projections derived from durable facts, never stored as truth.

## 9. State tables

### 9.1 Task generation lifecycle

| Derived state | Durable condition | Scheduler action |
| --- | --- | --- |
| `unmaterialized` | required expansion not closed, or scope not instantiated | wait; expose the roster or scope blocker |
| `blocked` | at least one clause can no longer be satisfied | emit terminal `skipped`, or hold for operator policy |
| `waiting_dependencies` | clauses unsatisfied but still satisfiable | none |
| `ready` | all clauses satisfied; no accepted commit for the desired generation | request budget, then admission |
| `budget_wait` / `budget_refused` | hard budget unavailable | wait, or fail per policy |
| `admission_wait` | no authority has granted capacity | stay queued with reason and age |
| `dispatching` | attempt grant persisted, placement requested | observe; cancel on timeout |
| `running` | attempt acknowledged | monitor heartbeat and deadline |
| `retry_wait` | last attempt ended retryably, retry time in the future | wake at the timestamp |
| `waiting_external` | manual, event, or timer task armed with no process | wait, holding no slot |
| `succeeded` | accepted commit exists for the desired generation | wake dependents |
| `failed` | permanent terminal attempt, no accepted commit | propagate through clauses |
| `cancelled` | terminal cancellation for the desired generation | propagate through clauses |
| `stale` | commit exists but generation or upstream lineage changed | schedule a new generation when dependencies permit |

Durable facts are generations, attempts, commits, cancellations and overrides,
expansions, reservations, and effect receipts.
Every label above is a projection of those.

### 9.2 Clause evaluation

For a clause, the related set is:

```text
related = mapping(downstream_task, upstream_closed_expansion)
```

Then:

- `require=succeeded, cardinality=all`: every related task has an accepted commit.
- `require=finished, cardinality=all`: every related task has a terminal outcome, and
  the binding delivers commit references plus failure descriptors.
- `cardinality=any`: at least one related task satisfies; closure decides whether an
  as-yet-unsatisfied clause can still become satisfied.
- empty related set: vacuous success for `all`, failure for `any`.

## 10. The reference reducer

The scheduler core is specified as a pure function:

```text
reduce(state, event, now) -> (state', commands)
```

No subprocesses, no filesystem, no clock of its own.
Time enters as a parameter so retry backoff and deadlines are testable without waiting.

Events are durable facts arriving: an expansion closed, an attempt started, an attempt
ended with a disposition, a commit accepted, a cancellation requested, a force issued, a
reservation granted or refused, a tick.

Commands are what the runtime should do next: materialize an expansion, request
admission, dispatch an attempt, schedule a retry at a time, accept a commit, cancel an
attempt, release a reservation, finalize the run.

Three contracts of this shape are easy to miss and are stated here so the engine
integration cannot miss them:

- **Commands are idempotent proposals, re-derived from state.** An un-acted command
  (`MaterializeExpansion`, `DispatchAttempt`) is emitted again on every event until the
  corresponding fact arrives.
  The runtime deduplicates; the reducer never remembers what it already proposed,
  because remembering is state and all state is in `state`.
- **There is no accept-commit command.** A commit is applied as a fact when a current,
  unfenced attempt ends successfully.
  An engine that needs a distinct acceptance step (validation, quarantine) owns that
  step in the runtime and reports its outcome as the attempt’s disposition.
- **The reference model interprets only the new semantics.** `KernelState` defaults its
  semantics version to the new contract; the legacy barrier interpretation of `needs`
  lives in the production engine, not here, and the degenerate-equivalence suite in
  Phase D is what ties the two together.

The reference model recomputes projections and commands from whole state on every event,
which is O(n) per event and quadratic over a full drain.
That is the right trade for an executable specification and the wrong one for the
engine: the production scheduler must maintain its ready set incrementally against these
semantics, not copy this shape.

This is the highest-leverage artifact in the project.
It makes the semantics testable without timing-dependent integration, and every later
phase is checked against it.

## 11. Invariants

The reference implementation must prove, by property and model tests:

1. No task starts before every dependency clause is satisfied.
2. No fan-in starts before the relevant expansion is closed.
3. At most one commit is accepted per task generation.
4. A stale attempt can never commit after a newer attempt is granted.
5. Restart and replay yield the same derived scheduler state.
6. Retry backoff survives restart.
7. An unrelated item’s failure does not block unrelated descendants.
8. An aligned failure blocks exactly the mapped descendants.
9. `collect_all` invalidation reaches the fan-in.
10. Force follows the same dependency mapping as readiness.
11. Empty rosters terminate correctly.
12. Duplicate keys fail materialization deterministically.
13. Missing subset keys fail before downstream execution.
14. Cancellation leaks no resource reservations.
15. Fairness eventually admits continuously waiting work when capacity frees.
16. Budget reservation is atomic and idempotent.
17. Late worker results are fenced.
18. Finalization freezes exactly the published generation.

## 12. Deliberately left open

- **Quota-namespace defaults.** How provider, account, and model or region compose into
  a default namespace key per adapter.
  Keyed only by execution profile is likely wrong in both directions: two profiles can
  share one account quota, one profile can span regional quotas.

- **Projection schema: drafted.** `metaproc:ProcessStatus/0.1`, implemented in
  `src/metaproc/kernel/projection.py` as a pure function of durable facts, so it is
  rebuildable by construction and nothing can schedule from it.
  Task views carry state, generation, attempt count, and one primary blocker; step views
  carry counts, an aggregate outcome, and coverage.
  Phase C makes it durable and versioned on disk; this settles the shape.

  The blocker vocabulary is the part that earns its keep: `dependency_unsatisfiable`,
  `dependency_pending`, `expansion_not_closed`, `scope_not_instantiated`,
  `retry_backoff`, `admission_wait`, `budget_wait`, `waiting_external`,
  `upstream_generation_changed`. Each names the specific upstream tasks or roster
  generation involved, because “pending” is what operators have today and it explains
  nothing. Precedence is deliberate: unsatisfiable outranks unclosed, which outranks a
  retry timer, matching the order in which an operator can act.

  Two honest gaps. `admission_wait` and `budget_wait` are defined but never returned,
  because the reducer does not model admission yet; a ready task reports no blocker
  rather than a fabricated one.
  And `dependency_unsatisfiable` is only observable to a caller inspecting state
  directly, since the reducer settles a blocked task to `skipped` in the same pass.

- **Scale envelope, in memory: confirmed.** The reference reducer holds 10^3 to 10^4
  tasks per run, at roughly 0.03s per scheduling pass over 2,400 tasks, scaling linearly
  in roster width. `tests/kernel/test_kernel_scale.py` keeps it that way.

  It did not start out that way, which is the reason to benchmark rather than assume.
  The first implementation looked up tasks, commits, and materialized keys by scanning
  tuples, so a scheduling pass was quadratic in roster width: 0.25s at 600 tasks and
  growing fourfold per doubling, which extrapolates to seconds per pass at cohort scale
  and hours to drain a run.
  Indexing the lookups fixed it.
  An accidental quadratic is invisible on a demo roster and fatal on a real one.

  Still open is the *durable* side: this measures in-memory scheduling only.
  Filesystem metadata load, per-task status writes, event-log volume, and resume time at
  the same envelope are unmeasured, and fair admission across scopes and composite
  scopes both assume they are affordable.

- **Threshold cardinality and `group_by`.** Modeled, not implemented, until a workload
  needs them.

## 13. What this RFC does not propose

Kept out on purpose, so the kernel stays small:

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

The kernel is: templates, closed expansions, mapped dependency clauses, task
generations, attempts, commits, admission claims, and effects.
Everything else is projection or policy.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
