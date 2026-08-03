---
type: is
id: is-01kz2mq7p6h59tkjenmw52t5pm
title: Dash-delimited typed ID grammar with dot-separated parsed interiors
kind: task
status: closed
priority: 0
version: 3
labels: []
dependencies:
  - type: blocks
    target: is-01kz2mq7ypjas5rv4zcjsrzbe9
created_at: 2026-08-03T01:44:55.237Z
updated_at: 2026-08-03T02:39:54.643Z
closed_at: 2026-08-03T02:39:54.642Z
close_reason: Implemented in f4b9af6; 3876 metaproc tests pass
---
ids.py: prefix-payload dash grammar; timestamped interiors switch to dots (run-20260408T003012Z.2555210000.foayjjhknb); rpartition parses on dot; tolerant parse_typed_id accepting [-_] at the boundary; bits become parameters; add typed_id_pattern(). Tolerance must never make run-abc equal run_abc.
