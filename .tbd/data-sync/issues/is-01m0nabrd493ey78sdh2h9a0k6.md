---
type: is
id: is-01m0nabrd493ey78sdh2h9a0k6
title: Scalar agent steps do not retry transient subprocess failures (S1)
kind: task
status: closed
priority: 2
version: 2
labels: []
dependencies: []
created_at: 2026-08-22T18:05:09.412Z
updated_at: 2026-08-22T18:23:18.138Z
closed_at: 2026-08-22T18:23:18.138Z
close_reason: "Done upstream by author in c5baaaf: scalar exit-code failures now classify and retry with log-tail extraction"
---
PR #25 review S1 (deferred): fan-out retries transient nonzero exits via classify_error (run_parallel.py:1013-1015 and pool path); _execute_agent_step keeps exit-code failures terminal. Decide whether scalar steps should classify subprocess errors too.
