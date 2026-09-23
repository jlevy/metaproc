---
type: is
id: is-01m0ycs676pbjek6skpx2h9z1e
title: Stabilize process-tree timeout regression under parallel test load
kind: bug
status: closed
priority: 1
version: 3
labels:
  - testing
  - reliability
dependencies: []
created_at: 2026-08-26T06:40:36.581Z
updated_at: 2026-08-26T06:50:10.458Z
closed_at: 2026-08-26T06:50:10.458Z
close_reason: "Fixed in public PR #49 at 46249c1; standalone make verify passed with 4,434 tests and all GitHub CI jobs are green."
resolution: null
duplicate_of: null
---
The mandatory pre-push verify gate intermittently fails test_agent_subprocess_timeout_kills_its_process_tree because the 0.2-second subprocess timeout can expire before two Python processes publish their PID handshake under a 10-worker suite. Preserve the timeout/cleanup behavior while giving startup a bounded, non-racy allowance; prove the focused test repeatedly and rerun the full gate.

## Notes

Raised the test-only timeout from an unrealistically startup-sensitive 0.2 seconds to a named 2-second bound. The test still proves TimeoutExpired and full descendant cleanup; the run-execution-context file passes 30 tests under logical-core xdist. Full pre-push verify remains the final gate.
