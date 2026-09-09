---
type: is
id: is-01m0zpdfftvam4ebsvsvq10wve
title: Bare resume re-enters completed scalar composites
kind: bug
status: open
priority: 1
version: 2
labels:
  - resume
  - composite
dependencies: []
created_at: 2026-08-26T18:48:13.039Z
updated_at: 2026-09-09T07:08:20.617Z
---
Verified still present at `d4fb019`, with the mechanism confirmed against code rather than inferred.

`_orchestrate` snapshots the prior projection (`run_process.py:4170`), then sets every step in `levels` to `pending` and adds it to `active_ids` (`:4178-4182`). Prior state is carried forward only for steps *not* in `active_ids` (`:4186-4187`), so on a bare resume nothing carries over. The all-pending map is persisted at `:4193`, before the level loop. That reset is deliberate: without it a monitor racing a resume observes a stale terminal state while new work is already running.

The completion check at `:4361` then calls `_is_step_completed`, which reads the per-task record first and falls back to `process-status.yaml` (`:1305-1325`) — i.e. to the buffer the caller just overwrote. The fallback is load-bearing for fan-out steps, where the projection is authoritative because there is no single per-task record. `:4361` already exempts *mapped* composites explicitly and deliberately.

A scalar composite is the one step kind with neither. `mark_running_at` has four call sites — code `:1549`, agent `:2513`, mapped-composite items `:3162`, manual `:3863` — and `_execute_composite_step` is not among them. So the fallback reads `pending`, the check returns False, and the child orchestrator is re-entered.

Cost, re-measured after mp-vl07 was fixed: child spec reparse and replan (including discovery for the child's mapped steps), child projection reset and re-derivation, `validate_process_outputs` over the child's outputs, and a fresh `process_start` plus a full set of `step_skip` records on the child's event stream, recursively for nested composites. Child leaves skip correctly from their own durable records, so no handler or agent runs and nothing is charged to a provider. Before mp-vl07 was fixed, a standalone code fan-out inside the child re-invoked every completed handler; that was the only path that did real work, and it is closed.

Deliberately not fixed for v0.4.0. The correct fix is to give composites a durable task record, which requires settling what an attempt means for a step whose work is a whole child DAG — tracked as `mp-5nko`, which this now depends on. Patching the fallback instead is the worse trade: it is load-bearing for fan-out steps, the snapshot it would read is the stale terminal state the reset exists to suppress, and the failure modes are asymmetric — the present defect repeats work, a wrong reuse predicate skips it.

Disclosed as a known gap in `docs/project/releases/v0.4.0.md` and recorded in `docs/project/design/backlog/arch-execution-model-backlog.md`.

Note for whoever picks this up: `tests/test_run_process.py:1773` (`test_force_reexecutes_a_real_composite_child`) pins "a bare resume does no child work" and stays green either way, because it never asserts the composite is *skipped*. A fix needs a new assertion on the skip, not just that existing test.
