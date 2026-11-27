---
type: is
id: is-01m4mycsvmhmn7hp5sa73zch05
title: "PR #105 A2: auth-check --live crashes when the Gemini config is refused"
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
created_at: 2026-10-11T07:38:48.046Z
updated_at: 2026-10-11T13:51:10.651Z
started_at: 2026-10-11T13:19:32.772Z
closed_at: 2026-10-11T13:51:10.650Z
close_reason: "Fixed in 05745e4 (auth-check live probe reports refused config). Disposition: https://github.com/jlevy/metaproc/pull/105#issuecomment-6109722539"
resolution: null
duplicate_of: null
---
Medium. Review A https://github.com/jlevy/metaproc/pull/105#pullrequestreview-5482267623. commands/auth_check.py:404-412 guards only build_command; prepare_env at :445 raises ValueError out of the command (cli.py:157-160 catches only CLIError). A legacy native_settings_scope system profile or a template without .gemini/settings.json gives a traceback and stops the all-adapters loop. Fix: report as a failed result.
