---
type: is
id: is-01m2h6e08ap8bh9mez5zfhqzmv
title: Investigate nondeterministic RunPool cancellation PID assertion
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
created_at: 2026-09-15T00:10:54.600Z
updated_at: 2026-09-15T00:10:54.600Z
---
During the PR #82 pre-push gate on 2026-09-15, tests/test_runpool_pool.py::TestRunPool::test_cancelling_pool_scope_cleans_running_and_queued_siblings observed the child PID immediately after pool shutdown even though active_count and pending_count were zero. The same test passed in the prior full make verify run and then passed 10/10 isolated serial reruns. Investigate whether the full-suite failure was delayed child reaping, zombie visibility, or PID reuse; preserve the guarantee that no live process from the cancelled launch remains, using process identity fencing if the assertion changes.
