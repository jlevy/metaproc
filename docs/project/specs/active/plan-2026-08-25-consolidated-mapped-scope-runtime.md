---
title: Consolidated Mapped-Scope Runtime
description: >-
  Add in-process mapped composite scopes using one recursive execution context and one
  run-owned RunPool, while retaining existing process, recovery, and inspection
  primitives.
author: Joshua Levy (github.com/jlevy) with LLM assistance
date: 2026-08-25
last_updated: 2026-08-26
status: Draft — Consolidated Review
category: plan
tracking_bead: mp-1c19
---
# Feature: Consolidated Mapped-Scope Runtime

## Overview

Metaproc can map agent and code steps over a roster, and it can evaluate a composite
process in-process. It cannot combine those operations: a composite step with `for_each`
is rejected.

That missing composition pushes callers toward flattening a reusable child process or
mapping a code handler that launches another `metaproc run-process` command.
The latter creates a child orchestration boundary for every item.
Leaf work, admission, failure evidence, and execution policy then sit behind the parent
rather than inside it.

This change permits `for_each` on a composite step.
Every item is an in-process child scope within one parent run.
The implementation reuses the existing recursive process evaluator, neutral fan-out
runner, RunPool, credential pools, task records, output validation, resume behavior, and
operator views.

The consolidation replaces a historical stack with one clean review from released
`main`. The stack remains useful as test and design evidence, but its commit history and
consumer-specific rationale are not part of this branch.

## Decision

Implement the smallest executable composition:

1. allow `mode: composite` with `for_each`;
2. invoke each child through the existing neutral fan-out machinery and recursive
   evaluator, without a child CLI or child orchestrator lease;
3. create one internal `RunExecutionContext` at the top-level command and reuse it in
   every recursive scope;
4. make that context own one RunPool for local resource-bearing mapped leaves in the
   initial single-profile topology;
5. retain existing process and step output declarations as the artifact boundary; and
6. project failures, status, pool state, and traces through existing operator surfaces.

Do not add a ready-task scheduler, workflow service, provider ontology, generic invoker
hierarchy, second memory controller, mutable budget ledger, or runtime artifact
registry.

## Authored Surface

The first slice introduces no new mode or mapping language.
It permits an existing combination:

```yaml
steps:
  - id: mapped-work
    mode: composite
    uses: deps.item-process
    inputs:
      roster:
        ref: prepare.roster
    for_each:
      over: roster
      bind: item
      bind_fields: [item, input_path]
      key: "{{item}}"
    with:
      ITEM: "{{item}}"
      INPUT_PATH: "{{input_path}}"
    outputs:
      report:
        path: "{{run.dir}}/mapped-work/{{item}}/report.md"
        kind: file
```

Each child resolves `run.dir` to its own scope root:

```text
<run>/<step>/<item-key>/
```

The mapped parent task retains the existing item-state address under
`.state/tasks/<step>/<item-key>/`. Item keys must be unique and path-safe before any
state or child directory is written.

Whole-scope retry, multi-host `gcp-worker` partitioning, and qualified per-item force
are rejected or deferred.
Child leaves retain their existing retry policies.
Ordinary resume re-enters only failed or invalidated child work.

## Runtime Invariants

### One orchestration authority

- The top-level command owns the run identity and orchestrator lease.
- Child scopes call the recursive evaluator directly.
- No child scope invokes a Metaproc CLI or acquires another orchestrator lease.
- Mapped scope evaluators are structural and do not consume executable-leaf capacity.

### One recursive execution context

`RunExecutionContext` carries immutable shared references and run policy:

- backend and placement settings;
- execution profiles and operator ceilings;
- force, skip, continue, and cancellation policy;
- credential-pool configuration;
- the run-owned executor for synchronous handlers and command supervision; and
- the run-owned RunPool and admission state.

Scope-local variables, plans, paths, and run directories remain explicit arguments.
The context is an internal container, not a new public service or singleton.

Synchronous handlers and command-backed code execute off the event loop.
Executor capacity is sized deliberately from the operator ceiling, and shutdown retains
executor, process-tree, credential, and admission ownership until started work is
terminal.

### One resource authority for mapped leaves

The existing RunPool governs local resource-bearing mapped leaves.
Scalar mapped agents submit prepared launches to the run-owned pool; mapped scopes do
not create direct launch controllers.
RunPool continues to own adaptive pressure response, subprocess supervision, events,
status, and process-tree cleanup.
Existing host admission remains the cross-run boundary.

In-process deterministic handlers retain the run-owned executor and executable-leaf
ceiling. Command-backed code remains on its existing supervised path until a measured
case proves that moving it into RunPool preserves semantics and improves control.

Weighted byte claims are deferred.
Add them only if a mixed-profile or concurrent-run test shows that the current RunPool
and host gate cannot maintain safety or useful utilization.

### Durable state and recovery

- Every mapped item writes one parent task attempt and result.
- The child process writes its existing process and leaf task state.
- Ordinary exceptions become terminal item failures only after sibling item scopes
  settle.
- Cancellation records a cancelled parent attempt before propagating.
- Resume revalidates declared outputs before reusing completed work.
- Runtime-discovered roster items are excluded from definition fingerprints; authored
  fan-out fields remain part of identity.
- Changed resolved run variables fail closed under an existing run identity.

### Graph and artifact boundaries

Failure propagation is evaluated per affected direct dependency.
A tolerant `require: finished` collection must not mask a separate success-required
dependency in the same graph.
The current resolved plan collapses two authored clauses that name the identical
upstream into one dependency entry; distinguishing a tolerant and strict clause on that
same upstream is deferred until a real process requires a richer dependency contract.

Existing child process outputs and mapped parent step outputs remain explicit and are
validated at their respective boundaries.
Because the mapped parent is planned in the parent scope, its output paths name the
child scope explicitly (for example, `{{run.dir}}/<step>/<item-key>/report.md`).
Automatic port projection, aliases, and a second composite I/O language are deferred.
Operator views rebuild lineage from declarations and accepted results rather than
introducing another artifact authority.

The runtime task/output view is therefore a rebuildable projection, not another durable
result or lineage record.
Each evaluated process scope persists a narrow projection of its exact resolved `Plan`
in `run-plan.yaml`: step identity, scalar-or-mapped shape, canonical mapped item keys,
output declarations, and fingerprints.
Opaque adapter configuration, environment, parameters, prompts, and fan-out item
payloads stay out of the record.
The view qualifies scalar and mapped tasks by scope, requires every child to match its
nearest parent composite declaration, validates mutable status against retained
attempts, and binds a consumable result to the exact successful attempt and recorded
current fingerprint.
If an upstream step creates the fan-out source after initial planning, runtime discovery
atomically refreshes the affected step’s canonical key set before dispatch.
A later resume replaces the set, so removed items cannot retain authority.
This keeps execution and artifact bindings reviewable without reconstructing the
orchestrator’s decisions or adding scheduler state.
When a recorded plan declares an executable scalar task, mapped item task, or composite
child scope but the corresponding durable state is absent, the view emits a typed
coverage gap instead of silently presenting the remaining records as complete.
Scalar composites are represented by their child scopes rather than synthetic parent
tasks; mapped composites retain parent item tasks because those records own each mapped
attempt and result.
Only declared, portable, available outputs of the declared kind enter
the accepted set.
Legacy unbound, stale, undeclared, missing, and external outputs remain
explicit diagnostics so partial hydration and definition drift cannot masquerade as
accepted evidence.

## Consolidated Review Domains

The pull request is one review surface, but its failure domains remain explicit:

| Domain | Required proof |
| --- | --- |
| Recursive execution | One context by identity, truthful ceiling, responsive event loop, bounded close |
| Credential policy | Scalar and fan-out paths use the selected pool policy and record bypasses durably |
| Lifecycle ownership | Cancellation and failure retain process, credential, executor, and admission ownership through cleanup |
| Graph propagation | Direct, transitive, sibling, tolerant-collector, and diamond shapes remain correct |
| Mapped scopes | Unique contained identity, isolated artifacts, bounded scope evaluation, mixed outcomes, failed-item resume |
| Shared RunPool | One run-owned pool, no direct scalar mapped launch, fresh admission on resume |
| Operator truth | CLI, status, events, trace, and pool rollup agree on terminal state and nested work |

### Review dispositions

The consolidated branch has received a fresh review independent of the historical pull
request stack.
“Fixed” means the change and focused regression are present on the branch;
the local exact-head and public CI gates below remain independent landing requirements.

| Finding | Disposition | Resolution |
| --- | --- | --- |
| R1: recursive scope discovery and containment | Fixed | Operator discovery now walks arbitrary nested runtime scopes and rejects symlink escapes. |
| R2: output-boundary documentation | Fixed | Documentation matches inherited variables plus `with` overlays and explicit child and parent outputs. |
| R3: single-profile pool topology | Fixed | A second profile fails before pool reuse; the operator limitation is explicit. |
| R4: stale terminal status during resume | Fixed | Active lease ownership outranks a carried terminal projection, and orchestration writes fresh running state. |
| R5: unsupported mapped worker topology | Fixed | Active-plan validation rejects it before any step or cloud dispatch runs. |
| R6: mixed clauses naming one upstream | Deferred | The resolved plan cannot distinguish those clauses; no current process requires widening the dependency model. |
| R7: scalar reservation ordering | Deferred | The ordering is bounded and safe; M2, M3, and concurrent-run measurements decide whether it harms utilization. |
| R8: diagnostic failure during credential completion | Fixed | Every post-acquisition diagnostic path now releases slot ownership. |
| R9: released claims in `Unreleased` | Fixed | The changelog now states only this branch’s delta from the released baseline. |
| R10: adapter-resolution failure during teardown | Fixed | Adapter lookup sits inside the unconditional slot cleanup boundary. |
| R11: scalar cancellation and teardown failure | Fixed | Existing scalar attempts become terminal while preserving the primary exception. |
| R12: cancelled pooled scalar ownership | Fixed | Per-submission cancellation drains the process and pool task before credential, host, or leaf release. |
| R13: code and mapped abort state | Fixed | Code cancellation and mapped nonstandard aborts now terminalize durable attempts before propagation. |
| R14: ambient auth in pooled cloud orchestration | Fixed | Cloud scalar and fan-out leaves both acquire pool slots; the dispatcher no longer hydrates one label as ambient auth and requires an explicit Batch identity for pool access. |
| R15: worker bootstrap guard test isolation | Fixed | Guard tests use an injected environment mapping, so the one-shot worker mutation cannot leak into later entrypoint tests. |
| R16: runtime run identity reconstruction | Fixed | The projection derives the task-record root from the persisted process name and run context, matching `run-process`. |
| R17: stale result accepted after retry | Fixed | New results name the exact successful attempt; legacy or mismatched results remain unaccepted diagnostics. |
| R18: current declarations relabel stale outputs | Fixed | Acceptance requires the current step fingerprint and an exact declared output port. |
| R19: missing or wrong-kind artifacts look consumable | Fixed | Availability and declared kind are checked before an output enters the accepted set. |
| R20: projection errors hide structural visualization | Fixed | Expected scan failures become typed warnings while the process graph remains available. |
| R21: runtime projection is not exposed in the browser | Fixed | The browser supplies its active run context and renders task and output facts. Cross-host process-spec portability is tracked separately as `mp-e3mg`. |
| R22: cross-scope task sorting is partial | Fixed | Task projection uses an explicit total ordering over scope, step, and optional item key. |
| R23: public projection embeds mutable runtime models | Fixed | A strict, narrow DTO carries only stable task, binding, and output facts; historical `VizModel/0.3` remains readable. |
| R24: external outputs abort hydrated views | Fixed | Nonportable output paths are retained as diagnostics and never treated as hydrated artifacts. |
| R25: rebuilt nested plans reject valid mapped outputs | Fixed | Every evaluated runtime scope atomically records its exact step projection and fingerprints; hydrated projection consumes that record and retains a bundle fallback for older runs. |
| R26: nested snapshots self-authorize stale scopes | Fixed | A child scope is visible only when its path matches a composite declaration, scalar-or-mapped shape, and canonical item key in the nearest accepted parent scope. Exact snapshots also exclude stale mapped task keys. |
| R27: full plans persist sensitive or unbounded fields | Fixed | The runtime record excludes params, environment, prompts, opaque adapter config, and fan-out item payloads. Only canonical scope keys remain; regression coverage proves that a 500-item expansion omits private payload fields. |
| R28: runtime plan schema fails open | Fixed | The standalone and SoftSchema registries publish the strict pure-YAML `RunPlanSnapshot/0.1` contract, validate real artifacts, and reject unknown versions. |
| R29: runtime-produced fan-out sources leave empty item authority | Fixed | Agent, code, aligned-chain, and mapped-composite discovery atomically refresh the existing scope snapshot before dispatch. End-to-end producer-to-mapped-leaf and producer-to-mapped-composite tests prove accepted projection, and resume coverage removes a stale item key. |
| R30: fan-out disposition collides with authored fields | Fixed | Discovery keeps framework disposition separate from authored item context. Canonical key resolution preserves every declared field, retains completed, cached, or running items, and excludes source-terminal items. |
| R31: missing runtime state appears as complete coverage | Fixed | The projection compares exact plan-declared scalar, mapped-item, and composite-scope coordinates with durable state and emits typed coverage gaps for every absent record. Fully snapshotted synthetic and mapped-composite execution regressions require an empty gap set. |

The superseded retry-later transport is excluded.
Dormant retry primitives remain under their separate removal-or-justification audit.
Unrelated cloud dispatch changes and a general scheduler are outside this pull request.

The consolidation audit compared the replacement against the complete superseded
implementation stack, not only its final diff.
Ninety code, test, and documentation files are common to both implementations.
The superseded-only material is a historical plan; the replacement carries this
consolidated plan and additional coordinator regressions.
Every superseded-only test name is accounted for by prerequisite coverage or by a
domain-neutral rename with the same assertion.
No executable behavior was silently dropped.

## Current Validation Status

Consumer smoke testing of the consolidated candidate found four generic integration
defects without changing the runtime design:

- equivalent relative and absolute log paths could assign the same provider evidence to
  different hierarchy owners during finalization and recovery, which defeated event
  deduplication; and
- Gemini’s native file tool continued to honor workspace ignore rules for a declared
  runtime input, despite the run directory being included in the invocation; and
- source-spec-free finalization could not distinguish mapped composite item segments
  from child step names using the original immutable resource snapshot; and
- the Metabrowser CLI loaded the Metaproc browser plugin without running the separate
  Metaproc CLI bootstrap, so completed-run reconstruction could not recognize
  consumer-registered softschema envelopes when runtime-produced fan-out sources were
  present.

The candidate now normalizes both path forms before resource ownership, records the
qualified mapped-composite step IDs in a strict resource snapshot v2 while retaining a
strict v1 reader, resolves mapped logs to their executable leaves and item keys during
recovery, and injects the file-filtering override through Metaproc’s invocation-scoped
Gemini settings. Regression coverage runs finalization in both path-form orders and
asserts exact provider meters, tokens, list cost, and tool calls; it also covers mapped
child process events, item/step-name collisions, and v1 snapshot compatibility.
The visualization sidekick now loads installed consumer plugins before typed plan
reconstruction, matching other Metaproc entry points without weakening source validation
or introducing a consumer-specific parser.
The complete framework gate passes with 4,494 tests and the same eight tracked
credential or infrastructure skips, plus lint, type checking, public-hygiene checks,
dependency audits, package construction, and installed-wheel smoke.

Two observations remain deliberately outside this correction: trace health should make
recovered tool errors easier to distinguish (`mp-czm0`), and Gemini tool declarations
must either become enforceable policy or stop implying confinement (`mp-y1l2`). Neither
requires another scheduler, ledger, or input-staging abstraction.

## Deferred Work

- multi-host mapped-composite partitioning;
- portable process-spec identity for cross-host hydrated browser views (`mp-e3mg`);
- a general ready-task scheduler or persisted dynamic expansion graph;
- weighted host claims and mixed-profile placement;
- automatic child-output declaration synthesis or aliasing;
- successful-item targeted force;
- scoped child-variable restriction beyond declared bindings;
- same-upstream mixed tolerant/strict dependency clauses;
- a standalone runtime artifact-lineage index;
- reordering scalar pool, host, and credential reservations unless the 10-item, 32-item,
  or concurrent-run smoke gates show starvation or material idle capacity;
- child-spec loading or plan memoization before a 10-item or 32-item smoke test shows
  material planning overhead; and
- making the shared context mandatory on compatibility-level leaf helpers before a
  separate API cleanup can remove direct test and library call sites safely;
- a generalized attempt-lifecycle abstraction or decomposition of `run_process.py`
  before end-to-end smoke proves the consolidated behavior and supplies stable seams;
- a dedicated executor for log-filter joins before telemetry shows contention on the
  standard executor; and
- speculative retry-later policy integration.

Promote a deferred item only after a named test demonstrates a concrete failure or
material performance limit.

## Testing and Landing Gates

### Framework verification

The consolidated exact head must pass:

- planner and identity tests for the authored combination, duplicate keys, path
  containment, unsupported retry, and unsupported topology;
- recursive policy, ceiling, executor, credential, cancellation, and process-tree
  failure injection;
- mapped child namespace, output validation, mixed outcome, and failed-item resume
  fixtures;
- a real composite run/resume/force cycle proving run policy reaches the child
  evaluator;
- graph propagation shapes;
- real RunPool mapped-agent integration with the direct scalar launcher made fatal;
- status, event, pool-rollup, and nested-trace regression coverage;
- compatibility tests for existing scalar composites and agent or code fan-out; and
- exact root and mapped-scope plan projections, parent item-key authorization, secret
  and fan-out-payload omission, pure-YAML schema validation and rejection, identity
  rejection, containment, legacy fallback, runtime-produced fan-out refresh and removal,
  and hydrated output binding; and
- complete `make verify` plus exact-head GitHub CI.

### Successive smoke ladder

Framework verification is followed by downstream testing against an immutable pin:

| Rung | Shape | Gate |
| --- | --- | --- |
| M0 | Network-free three-item child process | One parent, no child CLI or lease, isolated failure, failed-item-only resume |
| M1 | One real harness and one item | Inherited policy, one RunPool, truthful operator views |
| M2 | One harness and three items | Shared admission, mixed-outcome isolation, closed fan-in |
| M3 | Separate harness profiles at larger width | Profile and credential propagation, measured process-tree memory |
| M4 | Same process on constrained and high-memory hosts | Safe concurrency adapts without a process-spec change |
| M5 | Full downstream shadow | Stable artifacts, recovery drill, and objective baseline comparison |

Exact downstream run identities and artifacts belong in the downstream repository, not
in public Metaproc comments, pull request bodies, plans, or beads.

### Landing policy

This draft is a consolidation and review boundary, not merge authorization.
It remains unmerged until:

1. every known review finding has a fixed, rebutted, or explicitly deferred disposition;
2. the clean diff from released `main` contains no consumer-specific evidence or old
   private-bearing commit history;
3. full local verification and exact-head public CI pass; and
4. the exact head passes the applicable private downstream smoke gate.

## Tracking

`mp-0iy8` is the enclosing mapped-composite epic.
`mp-1c19` and `mp-nxs9` record the completed first consolidation and its original
verification evidence.
`mp-re9l` owns final merge readiness on the settled prerequisite baseline:

- `mp-nued` integrates that baseline while preserving a clean runtime review boundary;
- `mp-3170` accounts for every retained behavior from the superseded stack;
- `mp-gc19` removes consumer-specific material from public files and review surfaces;
- `mp-7crh` reruns focused and complete verification on the integrated exact head; and
- `mp-nbt1` publishes the final disposition map only after every prerequisite closes.

`mp-joix` owns the offline vertical slice.
`mp-rrfn` owns successive recovery, resource, and scale proof.
Deferred retry behavior remains under `mp-tibt`.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
