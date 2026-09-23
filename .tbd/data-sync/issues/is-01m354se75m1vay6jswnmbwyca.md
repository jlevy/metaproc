---
type: is
id: is-01m354se75m1vay6jswnmbwyca
title: "PR #87 review R1: fan-in document strips attempt log paths from item errors"
kind: bug
status: closed
priority: 1
version: 3
labels: []
dependencies: []
parent_id: is-01m354sdgzcdwabh7t9rkzcmjj
created_at: 2026-09-22T18:07:00.837Z
updated_at: 2026-09-22T18:59:52.961Z
closed_at: 2026-09-22T18:59:52.961Z
close_reason: "Fixed in 95a6ada on PR #93"
resolution: null
duplicate_of: null
---
High, transparency. engine/fan_in.py _without_attempt_paths, engine/command_diagnostics.py without_evidence_paths. Keep full errors; the outcome digest already ignores wording. PR https://github.com/jlevy/metaproc/pull/87
