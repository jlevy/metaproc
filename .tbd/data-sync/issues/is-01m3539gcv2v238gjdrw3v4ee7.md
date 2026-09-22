---
type: is
id: is-01m3539gcv2v238gjdrw3v4ee7
title: Stop reporting a pass when the write-boundary check could not run
kind: bug
status: open
priority: 1
version: 1
labels: []
dependencies: []
parent_id: is-01m352sgcsjsxpwnprykp8rpsv
created_at: 2026-09-22T17:40:50.203Z
updated_at: 2026-09-22T17:40:50.203Z
---
`engine/write_boundary.py:395-404`: `_git_status` catches `CalledProcessError`,
`FileNotFoundError`, and `OSError` and returns `{}`.

An empty snapshot flows into `repo_changes_since`, which returns `[]`, which the caller
reads as "the agent wrote nothing outside its declared surface". So any git failure —
git missing from `PATH`, index lock contention, a repo mid-rebase, a permissions problem
— **silently disables the write-boundary check** and the run reports clean.

This is the security-relevant instance of the blanket-drop idiom `filesystem-rules`
names: "turns 'I could not read half this tree' into 'this tree has fewer files than you
think', and the caller reports success". Here it turns "I could not verify the boundary"
into "the boundary held".

Fix: distinguish the outcomes. Either raise, or return a sentinel the caller must handle
as "boundary unverifiable" and surface that as a run-level warning or failure rather than
as a pass. A boundary check that cannot tell the operator it did not run is not a
boundary check.

Two more swallowed-error sites with the same shape and a similar consequence, worth
doing in the same pass:

- `engine/run_status.py:480-486` — `except OSError: pass` around `steps_root.iterdir()`.
  Per-step pool-status candidates go missing, `pool_alive` stays `False`, and
  `metaproc status` reports a live run as COMPLETE.
- `engine/run_status.py:530-536` — `except OSError: pass` around `run_dir.iterdir()` in
  the composite-scope orchestrator-liveness scan. Same COMPLETE-while-running outcome.

Lower-consequence members of the same family, for completeness:
`paths.py:219-222` (`_children` returns `[]`, so an unreadable composite scope vanishes
from status, rollup, and reconciliation), `engine/operations_summary.py:1855-1868`,
`engine/resource_rollup.py:526-535`, `runtime_projection.py:399-401`, and
`io/gz_io.py:109-114` — the last being the curated public reader, so an unreadable JSONL
stream is indistinguishable from an empty one for every consumer downstream of it.
