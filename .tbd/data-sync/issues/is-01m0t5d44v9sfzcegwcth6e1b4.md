---
type: is
id: is-01m0t5d44v9sfzcegwcth6e1b4
title: "Review PR #35: retain lifecycle ownership through cleanup"
kind: task
status: in_progress
priority: 1
version: 3
labels:
  - pr-review
dependencies: []
created_at: 2026-08-24T15:14:43.482Z
updated_at: 2026-08-24T15:38:49.618Z
---
Senior review of #35 (codex/gtia-v3-cancellation-safety). Touches resource_sampling and runpool backend: check whether the synchronous run_sampled_step_command event-loop block (finding F3c) is fixed, cancellation/cleanup ownership is correct, no orphaned process trees. Post review comment; follow up before merge.

## Notes

Review posted 2026-08-24: https://github.com/jlevy/metaproc/pull/35#issuecomment-5397585319 — verdict: changes requested. Must-fix: (1) BLOCKER LocalBackend.launch env={**os.environ,**prepared.env} (backend.py:227) re-injects scrub-by-pop keys → pooled step runs on ambient creds (ANTHROPIC_AUTH_TOKEN, CLAUDE_CODE_OAUTH_TOKEN etc.); fix launch env contract + child-side absence test; (2) HIGH poll→kill on reaped pid with pgid=pid, no reuse guard, 10s monitor window can killpg recycled sibling group; (3) HIGH kill() now raises (RuntimeError, dropped PermissionError) — RunPool.shutdown loop aborts, lane active-count inflates forever. Also: Ctrl-C never reaches cancellation machinery (reaper preempts asyncio SIGINT), success-path cleanup can fail exit-0 commands, handlers shielded without cancel hook, teardown bypasses auth_outcome event. FOLLOW UP: verify fixes land before merge.
