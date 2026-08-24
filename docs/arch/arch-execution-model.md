---
title: "Architecture: Execution Model"
description: Executable reference model for the task-level execution contracts
author: metaproc team
status: Draft
---
# Execution Model Reference Implementation

**Date:** 2026-08-16 (last updated 2026-08-23) **Status:** Draft

> **Maintenance**: This is a maintained architecture doc.
> Revise via `tbd shortcut revise-architecture-doc` (which prompts you to verify content
> against current code, then add a “Future Considerations” section).
> When you make non-trivial changes, bump the **last updated** date above.
> The full arch-doc index lives in
> [development.md § Architecture docs](../development.md#architecture-docs).
> 
> Companion docs (in `docs/arch/`): [arch-metaproc-core](arch-metaproc-core.md),
> [arch-runpool](arch-runpool.md), [arch-cloud-execution](arch-cloud-execution.md),
> [arch-testing](arch-testing.md).
> Design rationale: [execution-model-design.md](../execution-model-design.md); general
> model: [process-framework-concepts.md](../process-framework-concepts.md).

`src/metaproc/execution_model/` is the executable form of the
[execution model design](../execution-model-design.md): the durable facts a run is made
of, and a pure reducer over them.
It performs no I/O and reads no clock, so scheduler semantics are testable without
processes, files, or timing.
The production engine is checked against it; it does not replace the engine, and the
engine must not copy its shape (see § Scale).

## Package Layout

| Module | Owns |
| --- | --- |
| `model.py` | Durable facts as frozen dataclasses (`TaskKey`, `TaskRecord`, `AttemptRecord`, `CommitRecord`, `ExpansionRecord`, `StepTemplate`, `DependencyClause`), their enums, and `RunState` with indexed lookups |
| `reducer.py` | `reduce(state, event, now) -> (state, commands)`, clause evaluation, task-state projection, the force cascade, retry backoff |
| `projection.py` | The typed status view: `ProcessStatus` / `StepView` / `TaskView`, and the per-task blocker vocabulary |

Facts versus projections is the load-bearing distinction: generations, attempts,
commits, expansions, and cancellations are *facts*; readiness, blocking, and aggregate
status are *projections* of them and are never stored as truth.

## The Reducer Surface

Events are durable facts arriving: `ExpansionClosed`, `ExpansionFailed`,
`AttemptStarted`, `AttemptEnded`, `ForceIssued`, `Tick`. Time enters as a parameter, so
retry backoff and deadlines are testable without waiting.

Commands are what the runtime should do next: `MaterializeExpansion`, `DispatchAttempt`,
`ScheduleRetry`, `CancelAttempt`.

Three contracts of this surface are easy to miss:

- **Commands are idempotent proposals, re-derived from state.** An un-acted command is
  emitted again on every event until the corresponding fact arrives.
  The runtime deduplicates; the reducer never remembers what it already proposed,
  because remembering is state and all state is in `state`.
- **There is no accept-commit command.** A commit is applied as a fact when a current,
  unfenced attempt ends successfully.
  An engine that needs a distinct acceptance step (validation, quarantine) owns that
  step in the runtime and reports its outcome as the attempt’s disposition.
- **The reference model interprets only the new semantics.** The legacy barrier
  interpretation of `needs` lives in the production engine, and the
  degenerate-equivalence suite planned for the scheduler bring-up is what ties the two
  together.

## Task States, as Implemented

| Derived state | Durable condition | Scheduler action |
| --- | --- | --- |
| `unmaterialized` | required expansion not closed, or scope not instantiated | wait; expose the roster or scope blocker |
| `blocked` | at least one clause can no longer be satisfied | settle to terminal `skipped` with a reason |
| `waiting_dependencies` | clauses unsatisfied but still satisfiable | none |
| `ready` | all clauses satisfied; no accepted commit for the desired generation | dispatch |
| `running` | a live attempt exists | monitor |
| `retry_wait` | last attempt ended retryably, retry time in the future | wake at the timestamp |
| `succeeded` / `failed` / `cancelled` / `skipped` | terminal outcome for the desired generation | wake or propagate through clauses |

Recomputation after a force is **not** a scheduler state: the task reads `ready` or
`waiting_dependencies` and is dispatched normally, and the projection reports the
superseded commit as an `upstream_generation_changed` blocker so a post-force status
does not read like a stall.
The states the design names but the reducer does not yet model (`budget_wait`,
`admission_wait`, `dispatching`, `waiting_external`) are listed under § Future
Considerations.

Clause evaluation is a small relational core: the mapping selects related upstream tasks
over a *closed* expansion (an open roster answers “unknowable”, never “empty”);
`succeeded` requires an accepted commit, `finished` any terminal outcome; an empty
related set is vacuous success for `all` and failure for `any`; `broadcast` over a
roster that closed with other than exactly one key is deterministically dead.

## The Projection

`project(state)` builds `metaproc:ProcessStatus/0.1` as a pure function of durable
facts, so “rebuildable, never authoritative” holds by construction.
Task views carry state, generation, attempt count, skip reason, and one primary blocker;
step views carry counts, an aggregate outcome, and coverage, because partial success is
a normal product state.

The blocker vocabulary answers “why is this not running” with the specific upstream
tasks or roster generation named: `dependency_unsatisfiable`, `dependency_pending`,
`expansion_not_closed`, `scope_not_instantiated`, `retry_backoff`, `admission_wait`,
`budget_wait`, `waiting_external`, `upstream_generation_changed`. Precedence is
deliberate: unsatisfiable outranks unclosed, which outranks a retry timer, matching the
order in which an operator can act.
`admission_wait` and `budget_wait` are defined but never returned, because the reducer
does not model admission; a ready task reports no blocker rather than a fabricated one.

## Invariants and Tests

`tests/execution_model/` proves the semantics by property tests, one class per invariant
group: clause satisfaction, expansion closure (the premature-barrier case), commit
linearization, fencing across every disposition, item-scoped failure propagation,
deterministic retry backoff, transitive force with same-key narrowing, replay
determinism, projection contract, and the scale envelope.
The suite is verified by mutation, not trusted for passing: defeating the fence check or
treating an unclosed expansion as empty each fails exactly the test that owns that
semantics.

Those tests check the model against itself, which proves internal consistency and
nothing about whether the model describes Metaproc.
`test_engine_equivalence.py` closes that gap for the degenerate case: it resolves every
spec under `examples/` through the engine’s own `build_plan`, translates the resulting
steps into model templates, and asserts the model’s ready-set waves equal `topo_sort`’s
levels and that a failed step settles exactly the set `graph.downstream` blocks.
Step-scoped edges are the point of the comparison, because before item-aligned semantics
can replace the level walk, the model has to reproduce it.

## Scale

The in-memory envelope is confirmed at 10^3 to 10^4 tasks per run, roughly 0.03s per
scheduling pass over 2,400 tasks, linear in roster width, guarded by
`tests/execution_model/test_scale.py`. Lookups are indexed rather than scanned, which is
what keeps a pass linear; the guard exists because an accidental quadratic is invisible
at test size and fatal at full roster width.

The reference model recomputes projections and commands from whole state on every event,
which is O(n) per event and quadratic over a full drain.
That is the right trade for an executable specification and the wrong one for the
engine: the production scheduler must maintain its ready set incrementally against these
semantics, not copy this shape.
The durable side of the envelope is unmeasured: filesystem metadata load, per-task
status writes, event-log volume, and resume time.

## Adoption Path

The model is an oracle, not a future implementation, and its value is realized in two
steps that this repository owns.
Consumers may schedule them inside larger plans under their own phase names; the
framework states here what they are, so the steps mean the same thing regardless of who
schedules them.

**What is live today.** Some item-scoped semantics from this design already run in the
engine, bridged into the level walk rather than managed by a task-level scheduler:
[item-aligned chains](arch-metaproc-core.md#item-aligned-chains)
(`graph.item_aligned_chains` plus `engine/item_runner.py`), fan-in collections with
`require: finished`, per-step ceilings, declared retry on the code path, and a
content-failure retry loop on the non-fan-out agent path.
Each is unit-tested.
The chain, fan-in, and declared-retry semantics are also replay-checked against this
model by `test_replay_equivalence`; per-step ceilings are a concurrency width the
replayed state does not observe, and the agent loop’s budget is resolved from step
defaults the trace translation does not yet read.

### How Item-Aligned Resume Works Today

A **step** is a node in the process spec.
A fan-out creates one **task** for each item, addressed by `(step, item key)`. For
example, a three-step chain over item `alfa` contains the tasks `(stage-a, alfa)`,
`(stage-b, alfa)`, and `(stage-c, alfa)`. `align: same_key` makes each downstream task
depend on the preceding task for the same item.
It does not depend on the other items in that step.

A **resume** is another invocation with the same run ID. It reads the durable records
left by the earlier invocation and reuses work recorded as complete.

The production engine still schedules topological levels of steps.
It implements a linear item-aligned chain through three pieces of control flow:

1. `graph.item_aligned_chains` identifies a maximal linear `same_key` chain over one
   item source. The orchestrator accepts the chain only when every member uses
   `mode: code`.
2. The level walk skips members after the head because `engine/item_runner.py` runs
   those members as part of the chain.
3. The chain head is the level walk’s only dispatch point for the whole chain.

This bridge leaves two completion views in the same loop:

| Completion view | Question | Durable source |
| --- | --- | --- |
| Step aggregate | Does the outer walk record this step as complete? | `.state/process-status.yaml` |
| Task state | Did this item complete this step? | `.state/tasks/<step>/<item>/status.yaml` |

Ordinary resume can use the step aggregate to skip a step.
For completion reuse inside an aligned chain, task state must decide.
If `(stage-a, alfa)` is complete but `(stage-b, alfa)` is incomplete, resume must reuse
the first task, run the second, and continue through any later incomplete tasks.
The aggregate fact “stage-a is complete” says nothing about `(stage-b, alfa)`.

The chain head therefore has two roles that must remain separate: it owns its own
fan-out tasks, and it is the dispatch point for every member of the chain.
Applying the ordinary completed-step skip to the first role also bypasses the second.
The later members have already been removed from the level walk, so none gets another
chance to run or report a skip.
The command can exit without an error or a chain message while leaving the incomplete
task untouched.

When deciding whether to reuse prior work, resume routes a chain head to the chain
executor instead of applying the outer completed-step skip.
`_discover_chain_items` enumerates the full item source, and the executor checks
completion for each `(step, item)` pair.
It reuses `completed` and `cached` tasks and invokes tasks with any other state or no
status record. The behavioral regression in `tests/test_item_aligned_chain_resume.py`
removes one later task’s status and output for a completed item, resumes without
`--force`, and verifies that the task runs while the item’s other completed tasks are
reused. `--force` has different semantics: it explicitly invalidates a step and
downstream work rather than recovering work that has no completed task record.

Aligned execution remains limited to linear `mode: code` chains.
Agent-mode alignment, item-scoped forks, and general mapped dependencies require the
task-level scheduler described below.

**Step one, durability: the engine’s durable facts adopt the model’s vocabulary.** This
is landing as independent reviewable slices.
The first slice is live on orchestrated and waited task paths: each launch creates a
typed `TaskAttemptRecord` under the task’s `attempts/` subtree before execution and
finalizes it once with the disposition and failure class.
`status.yaml` points to the current attempt, replay prefers the exact history, and
historical runs fall back to the legacy status and latest-launch records.
Success is terminal only after the validators owned by that execution path have run.
In particular, a local fan-out keeps successful attempts live until its step-wide write
boundary check finishes; outputless tasks still receive a completed result.
If valid output is discovered after a durable attempt has already ended as failed, the
framework refuses to rewrite history.
A later commit/adoption fact must explain why that artifact is accepted.

The remaining slices add task-generation state and fenced commit manifests,
attempt-private staging and publication, and explicit mixed-generation compatibility
tests. The direct per-task `attempt.yaml` remains a compatibility snapshot while those
readers exist; it is not attempt history.
Admission waits and class-specific resource retry budgets still need to become first-
class scheduler facts; until then, credential-capacity waits remain a known production-
to-reference-model gap tracked with the admission work.
This step exists on its own operational merits, because the corruption class it removes
is already observed on a single host: a step recording a timeout over an attempt that
later succeeded, and completed-but-invalid outputs reused forever by resume.
The replay harness already exists in walking-skeleton form: `execution_model/trace.py`
reads a completed run’s attempt facts, with a historical status fallback, as the
reducer’s six events (`ExpansionClosed`, `ExpansionFailed`, `AttemptStarted`,
`AttemptEnded`, `ForceIssued`, `Tick`) and `test_replay_equivalence` asserts
terminal-state agreement on a real engine run in CI, which is what retired the “checked
only against itself” caveat for the live semantics.
The first slice retires the replay skeleton’s concrete undercount: a task launched three
times now persists and replays three records with their actual dispositions.
Durable expansion records still have to replace roster reconstruction from task
directories; durable `ProcessStatus` rides the same broader increment.
Tracked as `mp-f07i`, `mp-rfnm`, `mp-2wtc`, and `mp-g315` under `mp-82ls`.

**Step two, the task-level scheduler: demand-driven, never speculative.** The remaining
gap between the graft and the model is exactly the semantics the level walk cannot
express, and each names its own trigger:

| Model semantics absent from the engine | Trigger that justifies building it |
| --- | --- |
| Item streaming across forks (aligned chains are linear, single-consumer) | two consumers need the same producer’s items without a barrier |
| Closed dynamic expansions and re-expansion generations | a roster grows mid-run, e.g. promotion-to-depth producing items while consumers run |
| Derived-subset lineage | a promoted subset must stay item-aligned to its parent roster instead of re-barriering |
| Per-item causal force with mapped invalidation | an operator needs to re-run one item through a chain without invalidating siblings |
| Deterministic fair admission across step scopes | starvation is observed between concurrently expanding steps |

Each increment, when triggered, lands against the replay harness from step one, so the
model checks it on arrival.
Building the scheduler before a trigger fires would be speculative; the design is done,
the price of waiting is low, and the price of building unused machinery is the
machinery.

## Relationship to the Engine’s Own Records

The model’s records are a parallel vocabulary, not a replacement, and two of them meet
engine types that the durability step below has to reconcile.

`execution_model.model.AttemptRecord` and `metaproc.models.runtime.TaskAttemptRecord`
describe the same real-world fact: one execution try with an identity, generation, fence
epoch, and disposition.
Trace maps the runtime disposition by value and maps its `failure_class` to the model’s
`failure_category`. `metaproc.models.runtime.AttemptRecord` is the older
`.state/attempt.yaml` launch snapshot; it lacks terminal history and is no longer the
source replay prefers.

`ProcessStatus` declares `metaproc:ProcessStatus/0.1` and follows the repo’s
schema-token field naming, but it is a frozen dataclass rather than a Pydantic model, so
the schema registry cannot resolve the token yet.
That is correct while the projection is in-memory only: the registry indexes artifacts
that reach disk. The durability step, which makes the projection durable, is also what
gives it a Pydantic model and a registry entry, and the token is chosen now so that step
is a registration rather than a rename.

## Future Considerations

### Open Questions

The reducer does not model admission and budget reservation, finalization and effects,
`group_by`, threshold cardinality, or the legacy barrier semantics of `needs`. The
design specifies admission claims and authorities, while RunPool remains the local
implementation.

Retry policy is data on `StepTemplate` rather than a scheduler constant, so the model
can replay a spec whose policy differs from the defaults.
The semantics version belongs to the resolved plan and must be enforced by the compiler;
storing it in scheduler state would not enforce anything.

### Potential Improvements

The two implementation increments in the adoption path remain the relevant improvements:
persist attempts and task generations as durable facts, then replace the level walk and
its aligned-chain bridge with an incremental task-level scheduler.
The trigger table above defines when the second increment is warranted.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
