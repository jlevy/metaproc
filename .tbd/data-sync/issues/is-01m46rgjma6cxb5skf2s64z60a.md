---
type: is
id: is-01m46rgjma6cxb5skf2s64z60a
title: Preserve mapped-agent batch-size fallback under the shared run pool
kind: bug
status: in_progress
priority: 2
version: 3
delegate: codex@spud10.local
labels: []
dependencies: []
hold: null
hold_until: null
created_at: 2026-10-05T19:26:38.206Z
updated_at: 2026-10-05T19:29:45.001Z
started_at: 2026-10-05T19:27:12.011Z
---

## Notes

Review reproduced batch_size=1 admitting three concurrent mapped-agent subprocesses when no run maximum was supplied. Two sibling mapped schedulers now retain their local batch-size fallback while sharing the run controller. An explicit run maximum overrides batch size; authored step ceilings and adapter/profile limits still bound admission. Consumers checked: _execute_fan_out_step, _run_agent_pool fill capacity, RunPool global permits, and sibling step schedulers. The four-case provider-free subprocess regression failed on the batch fallback before the fix and passes after it. Lower-layer fix awaits review, commit, and CI.
