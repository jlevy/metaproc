---
title: "Architecture: Semantic Kernel"
description: Executable reference model for task-level scheduling semantics
author: metaproc team
status: Draft
---
# Semantic Kernel Reference Model

**Date:** 2026-08-16 (last updated 2026-08-16) **Status:** Draft

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
> Design rationale: [semantic-kernel-design.md](../semantic-kernel-design.md); general
> model: [process-framework-concepts.md](../process-framework-concepts.md).

`src/metaproc/kernel/` is the executable form of the
[semantic kernel design](../semantic-kernel-design.md): the durable facts a run is made
of, and a pure reducer over them.
It performs no I/O and reads no clock, so scheduler semantics are testable without
processes, files, or timing.
The production engine is checked against it; it does not replace the engine, and the
engine must not copy its shape (see § Scale).

## Package layout

| Module | Owns |
| --- | --- |
| `model.py` | Durable facts as frozen dataclasses (`TaskKey`, `TaskRecord`, `AttemptRecord`, `CommitRecord`, `ExpansionRecord`, `StepTemplate`, `DependencyClause`), their enums, and `KernelState` with indexed lookups |
| `reducer.py` | `reduce(state, event, now) -> (state, commands)`, clause evaluation, task-state projection, the force cascade, retry backoff |
| `projection.py` | The typed status view: `ProcessStatus` / `StepView` / `TaskView`, and the per-task blocker vocabulary |

Facts versus projections is the load-bearing distinction: generations, attempts,
commits, expansions, and cancellations are *facts*; readiness, blocking, and aggregate
status are *projections* of them and are never stored as truth.

## The reducer surface

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
- **The reference model interprets only the new semantics.** `KernelState` defaults its
  semantics version to the new contract; the legacy barrier interpretation of `needs`
  lives in the production engine, and the degenerate-equivalence suite planned for the
  scheduler bring-up is what ties the two together.

## Task states, as implemented

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
States the design names but the reducer does not yet model — `budget_wait`,
`admission_wait`, `dispatching`, `waiting_external` — are listed under § Boundaries.

Clause evaluation is a small relational core: the mapping selects related upstream tasks
over a *closed* expansion (an open roster answers “unknowable”, never “empty”);
`succeeded` requires an accepted commit, `finished` any terminal outcome; an empty
related set is vacuous success for `all` and failure for `any`; `broadcast` over a
roster that closed with other than exactly one key is deterministically dead.

## The projection

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

## Invariants and tests

`tests/kernel/` proves the semantics by property tests, one class per invariant group:
clause satisfaction, expansion closure (the premature-barrier case), commit
linearization, fencing across every disposition, item-scoped failure propagation,
deterministic retry backoff, transitive force with same-key narrowing, replay
determinism, projection contract, and the scale envelope.
The suite is verified by mutation, not trusted for passing: defeating the fence check or
treating an unclosed expansion as empty each fails exactly the test that owns that
semantics.

## Scale

The in-memory envelope is confirmed at 10^3 to 10^4 tasks per run, roughly 0.03s per
scheduling pass over 2,400 tasks, linear in roster width, guarded by
`tests/kernel/test_kernel_scale.py`. It did not start that way: the first implementation
scanned tuples per lookup and a pass was quadratic in width, which is why the envelope
is benchmarked rather than assumed.

The reference model recomputes projections and commands from whole state on every event,
which is O(n) per event and quadratic over a full drain.
That is the right trade for an executable specification and the wrong one for the
engine: the production scheduler must maintain its ready set incrementally against these
semantics, not copy this shape.
The durable side of the envelope — filesystem metadata load, per-task status writes,
event-log volume, resume time — is unmeasured.

## Boundaries

Not modeled in the reducer, deliberately: admission and budget reservation (the design
specifies claims and authorities; RunPool remains the local implementation),
finalization and effects, `group_by` and threshold cardinality, and the legacy barrier
semantics of `needs`. Ten review findings from the 2026-08-16 multi-angle pass are
tracked for fix before the scheduler builds on this package; the durable list lives with
the PR record.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
