---
type: is
id: is-01m354samjsw035ghb1xvetz63
title: "PR #86 review R1: identity-variable refusal on resume should record and warn, not refuse"
kind: bug
status: closed
priority: 1
version: 3
labels: []
dependencies: []
parent_id: is-01m354s9wem4g14nhcnb7z1gvw
created_at: 2026-09-22T18:06:57.170Z
updated_at: 2026-09-22T18:59:51.543Z
closed_at: 2026-09-22T18:59:51.543Z
close_reason: "Fixed in 95a6ada on PR #93"
resolution: null
duplicate_of: null
---
High. src/metaproc/commands/run_process.py _validate_run_config: a changed, added, or removed variable raises CLIError. Record a launch_config_change event with old/new, warn, and proceed; drop the provenance opt-out. PR https://github.com/jlevy/metaproc/pull/86
