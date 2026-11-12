---
type: is
id: is-01m76jst1k2rr1rsafzhx8bvyv
title: Mapped-step reuse is decided by one arbitrary item's result record
kind: bug
status: open
priority: 1
version: 2
labels: []
dependencies:
  - type: blocks
    target: is-01m76jt5bwfqa7zkze6vva62tj
parent_id: is-01m76a30djgz1eds7sfd07wq3b
created_at: 2026-11-12T00:33:54.725Z
updated_at: 2026-11-12T00:34:06.330Z
---
Found by review B of PR #121 (finding B1, High):
https://github.com/jlevy/metaproc/pull/121#pullrequestreview-5867092636
Line references are at dee3914e5e001aace3abc728d81b0c6f859845e1; the defect predates PR #121.

The level walk resets each active step's `process-status.yaml` mirror entry to `pending`
at launch start (`src/metaproc/commands/run_process.py:5792-5817`), so
`_maybe_cascade_for_fingerprint` takes a mapped step's recorded hash from
`build_step_runtime_index` (`src/metaproc/engine/dep_state.py:314-345`, `:370-391`). That
function overwrites `result_step_hash` with each item's `result.yaml` it visits and keeps
the last one. Item reuse never reads `step_hash`: `_item_task_is_done`
(`run_process.py:2166-2200`), used by the code fan-out filter and by the chain executor's
`_is_done` (`:2917-2923`), checks status and outputs only, and the agent fan-out path
relies on discovery, which does the same (`src/metaproc/engine/discovery.py:176-190`).

Any sequence that leaves one step's items under different hashes is decided by directory
order. Reproduced on `main` (review B's `pb1b_def_change.py validated_outputs def`): a
mapped code step completes four items, its definition is edited, and one item (T01) is
re-run with `metaproc run-step --step each --item T01`; the next resume prints
`no actionable items (4 already complete)` and three items keep outputs from the old
definition. `run-parallel --items` writes per-item records the same way
(`src/metaproc/commands/run_parallel.py:666`).

Fix (from the review): compare per task. In `_item_task_is_done`, treat a completed item
whose `result.yaml` `step_hash` differs from the launch's fingerprint as not done, and give
the agent fan-out path the same check after discovery. Make `build_step_runtime_index`
keep every distinct hash it sees, ignoring records with none, and have
`_maybe_cascade_for_fingerprint` cascade when any of them differs from the current
fingerprint. Add a run-level test parametrized over all four item keys, asserting that
every output reflects the edited definition.
