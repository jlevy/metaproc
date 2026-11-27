---
type: is
id: is-01m4myd5bx78cntzw3hn79975s
title: "PR #105 A7: no test pins planning drop/strict refusal of a legacy system scope profile"
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m4mybyqptgk7zm6660wmwtzb
created_at: 2026-10-11T07:38:59.807Z
updated_at: 2026-10-11T13:51:11.909Z
closed_at: 2026-10-11T13:51:11.909Z
close_reason: "Fixed in 79bccc2 (planning tests for legacy system scope, falsified against base adapter). Disposition: https://github.com/jlevy/metaproc/pull/105#issuecomment-6109722539"
resolution: null
duplicate_of: null
---
Low suggestion. Review A https://github.com/jlevy/metaproc/pull/105#pullrequestreview-5482267623. tests/test_adapter_config_validation.py:185-205 covers claude-code-cli only. Fix: gemini-cli build_plan test with a profile file, warning text, and ValueError under METAPROC_ADAPTER_STRICT=1.
