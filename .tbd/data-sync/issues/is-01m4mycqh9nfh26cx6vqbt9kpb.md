---
type: is
id: is-01m4mycqh9nfh26cx6vqbt9kpb
title: "PR #105 A1: authored step env can override the Gemini private home and settings variables"
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
created_at: 2026-10-11T07:38:45.621Z
updated_at: 2026-10-11T13:51:10.310Z
started_at: 2026-10-11T13:24:06.587Z
closed_at: 2026-10-11T13:51:10.309Z
close_reason: "Fixed in a5b9756 (StepEnvValidator.validate_step_env; planning drops or refuses Gemini-owned step env keys). Disposition: https://github.com/jlevy/metaproc/pull/105#issuecomment-6109722539"
resolution: null
duplicate_of: null
---
Medium. Review A https://github.com/jlevy/metaproc/pull/105#pullrequestreview-5482267623. engine/runtime.py:262-264, commands/run_process.py:2441-2442 and :3261-3262, commands/run_parallel.py:961 and :1454 apply the step env after prepare_env; models/authored.py:629 is a plain dict. A gemini-cli step env naming GEMINI_CLI_HOME (user scope) or GEMINI_CLI_SYSTEM_SETTINGS_PATH silently drops Metaproc native settings. Review B adds GEMINI_SYSTEM_MD and GEMINI_CLI_SYSTEM_DEFAULTS_PATH to the owned-key list. Fix: treat like a rejected config key at planning (warn+drop non-strict same-family, raise strict/cross-family) or an adapter hook naming owned env keys.
