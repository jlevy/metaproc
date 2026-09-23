---
type: is
id: is-01kyx385qhs8bt0wzr4r4d25mh
title: Track 0.3 test-visible renames
kind: task
status: closed
priority: 2
version: 3
labels:
  - softschema
dependencies:
  - type: blocks
    target: is-01kyx38gn4gwmp93rst4psbm0x
parent_id: is-01kyx37mj1agq5zha1x5gn574f
created_at: 2026-07-31T22:03:23.760Z
updated_at: 2026-07-31T22:27:49.901Z
closed_at: 2026-07-31T22:27:49.900Z
close_reason: Assertions and fixture ids updated; 3784 tests pass on 3.12/3.13/3.14
---
0.3 removes the softschema_format_version key from compiled sidecars and renames structural error kind 'schema_sidecar_missing' to 'schema_missing'. Update assertions. Move fixture contract ids (sample.v1, test.v1, test.ticker.v1, earnings.prediction.v1, example.sample.v1, other.sample.v1) onto the validated grammar.
