---
type: is
id: is-01m354sc2c35vpfq4m1dmys013
title: "PR #86 review R3: process name, run dir, and step-variant mismatches should record and warn like variables"
kind: bug
status: closed
priority: 2
version: 3
labels: []
dependencies: []
parent_id: is-01m354s9wem4g14nhcnb7z1gvw
created_at: 2026-09-22T18:06:58.636Z
updated_at: 2026-09-22T18:59:56.353Z
closed_at: 2026-09-22T18:59:56.352Z
close_reason: "run_dir and step variants now record and warn (95a6ada, PR #93); process name still refuses because task identity embeds <process>/<RUN_ID>"
resolution: null
duplicate_of: null
---
Medium. run_process.py _validate_run_config (process, run_dir) and _resume_step_variants refuse with no escape hatch. Same record-and-warn posture, same event. PR #86
