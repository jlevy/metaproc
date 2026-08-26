---
type: is
id: is-01m0xthck1gm7b131mwrpstmxd
title: "PR #48: remove ambient OAuth injection from pooled scalar orchestration"
kind: bug
status: closed
priority: 0
version: 5
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels: []
dependencies:
  - type: blocks
    target: is-01m0xrg6jeywxa1hwns3eay01m
parent_id: is-01m0xrg4vr6n4znzxz0kkxxxt7
created_at: 2026-08-26T01:21:46.592Z
updated_at: 2026-08-26T01:25:49.839Z
closed_at: 2026-08-26T01:25:49.839Z
close_reason: The duplicated ambient-auth path is removed and the focused regression suite is green.
resolution: null
duplicate_of: null
---
The GCP orchestrator dispatcher still hydrates the first included pool label as ambient CLAUDE_CODE_OAUTH_TOKEN. Consolidated scalar agent steps now acquire a credential lease and refuse ambient auth, so the legacy injection conflicts with the one-pool design. Remove the workaround, preserve explicit runtime identity requirements, and add a combined-path regression.

## Notes

Fixed in the PR #48 worktree: removed legacy first-label ambient OAuth hydration, retained pool-user propagation, required an explicit Batch service account whenever the GCP Secret Manager auth pool is selected, and added combined-path regressions. Focused auth/dispatch suite: 48 passed; Ruff clean. R14 records the disposition in the public plan.
