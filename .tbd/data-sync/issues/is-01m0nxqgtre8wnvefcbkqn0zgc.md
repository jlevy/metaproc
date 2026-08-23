---
type: is
id: is-01m0nxqgtre8wnvefcbkqn0zgc
title: Scope YAML repair and conform to agent-authored output only
kind: bug
status: closed
priority: 1
version: 2
labels: []
dependencies: []
created_at: 2026-08-22T23:43:37.815Z
updated_at: 2026-08-22T23:52:44.778Z
closed_at: 2026-08-22T23:52:44.778Z
close_reason: "Landed in PR #27."
---
run_parallel's mode:code batch loop repaired declared outputs inline while run_process's code path did not; the two executors disagreed. The loop was also the third repair call site a0c8a0a missed, so it resolved paths by basename with no template rendering. Landed in PR #27. This bead replaces the nonexistent mp-24hi referenced in that PR's original description.
