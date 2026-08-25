---
title: Consolidated Mapped-Scope Runtime
description: >-
  Add in-process mapped composite scopes using one recursive execution context and one
  run-owned RunPool, while retaining existing process, recovery, and inspection
  primitives.
author: Joshua Levy (github.com/jlevy) with LLM assistance
date: 2026-08-25
last_updated: 2026-08-25
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

The superseded retry-later transport is excluded.
Dormant retry primitives remain under their separate removal-or-justification audit.
Unrelated cloud dispatch changes and a general scheduler are outside this pull request.

## Deferred Work

- multi-host mapped-composite partitioning;
- a general ready-task scheduler or persisted dynamic expansion graph;
- weighted host claims and mixed-profile placement;
- automatic child-output projection;
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

`mp-1c19` owns this clean consolidation and plan.
`mp-0iy8` is the enclosing mapped-composite epic.
`mp-nxs9` is the exact-head framework verification gate.
`mp-joix` owns the offline vertical slice.
`mp-rrfn` owns successive recovery, resource, and scale proof.
Deferred retry behavior remains under `mp-tibt`.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
