---
type: is
id: is-01m4myd6jykj5142evnkeq57k9
title: "PR #105 A8: prepare_env silently replaces an inherited GEMINI_CLI_HOME"
kind: bug
status: closed
priority: 2
version: 3
delegate: claude-code@spud10
labels: []
dependencies: []
parent_id: is-01m4mybyqptgk7zm6660wmwtzb
hold: null
hold_until: null
created_at: 2026-10-11T07:39:01.080Z
updated_at: 2026-10-11T13:51:12.241Z
started_at: 2026-10-11T13:33:20.109Z
closed_at: 2026-10-11T13:51:12.240Z
close_reason: "Fixed in 06d874f (log on replacing inherited GEMINI_CLI_HOME). Disposition: https://github.com/jlevy/metaproc/pull/105#issuecomment-6109722539"
resolution: null
duplicate_of: null
---
Low suggestion. Review A https://github.com/jlevy/metaproc/pull/105#pullrequestreview-5482267623. adapters/gemini_cli.py:561 overwrites an operator-set GEMINI_CLI_HOME. Fix: log.info naming both paths.
