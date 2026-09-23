---
type: is
id: is-01m354sewwdfzrn64z7f9xtt1j
title: "PR #87 review R2: status.yaml.stale beside a newer status.yaml keeps a re-run step reported as invalidated"
kind: bug
status: closed
priority: 1
version: 3
labels: []
dependencies: []
parent_id: is-01m354sdgzcdwabh7t9rkzcmjj
created_at: 2026-09-22T18:07:01.532Z
updated_at: 2026-09-22T18:59:53.637Z
closed_at: 2026-09-22T18:59:53.637Z
close_reason: "Fixed in 95a6ada on PR #93"
resolution: null
duplicate_of: null
---
High, transparency. engine/dep_state.py _scan_step_task_files counts any .stale file. Count it only when no sibling status.yaml exists. PR #87
