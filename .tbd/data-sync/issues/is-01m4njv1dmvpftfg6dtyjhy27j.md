---
type: is
id: is-01m4njv1dmvpftfg6dtyjhy27j
title: Gemini private home is removed while a detached run-step agent still runs
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
created_at: 2026-10-11T13:36:06.052Z
updated_at: 2026-10-11T13:36:06.052Z
---
Found while addressing PR #105 (not a review finding). Under the default native_settings_scope 'user', GeminiCliAdapter.prepare_env creates each launch's GEMINI_CLI_HOME inside a process-scoped tempfile.TemporaryDirectory, whose finalizer removes it when the Metaproc process exits normally. 'metaproc run-step' in detached mode (engine/runtime.py launch_step, called from commands/run_step.py) starts the agent with start_new_session=True and then exits, so the detached Gemini child's home (its settings.json and the directory it writes session state to) is removed while it runs, possibly before the CLI has read its settings. Reproduced: a process that calls prepare_env and exits leaves no home behind (the printed path no longer exists). Not reproduced: what gemini-cli does when its home disappears mid-session (no live session was run). Pre-existing for the old system-settings file and GEMINI_SYSTEM_MD file in the same directory, and for opt-in user scope; PR #105 makes it the default path. Fix direction (design decision): let a detached launch own its home's lifetime (for example a home under the run's logs directory, or a launch-completion hook the detached supervisor calls), which is the same lifecycle question as mp-1qjt (reclaim a home when its child exits).
