---
type: is
id: is-01m0v08t3404q7dh7y74sybbad
title: "PR #35 I3: stop pool-kill CancelledError leaking credential leases"
kind: bug
status: open
priority: 1
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-24T23:04:13.667Z
updated_at: 2026-08-24T23:04:13.667Z
---
Merge blocker. _run_process synthesizes CancelledError when _shutdown_event is set (pool kill sentinel) and the fan-out consumer catches only Exception at run_parallel.py:2027 — the escape skips the done-loop, leaks the credential lease with no auth_outcome, leaves status running, and unwinds the run as cancelled. Also cover the pre-existing fan-out cancel path where finally: pool.shutdown() abandons active entries so _pool_teardown never runs. Fix: catch BaseException with a terminal teardown path. Review: pull/35 comment (N1, F1); holistic ledger #3.
