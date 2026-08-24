---
type: is
id: is-01m0v08x9aned3w5cybk127eyp
title: "Process: CI on stacked heads; stack and spec-change rules"
kind: task
status: open
priority: 1
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-24T23:04:16.937Z
updated_at: 2026-08-24T23:04:16.937Z
---
The CI workflow runs only for main-based PRs, so no upper rung of the six-deep stack has ever run CI at its head and several PR bodies cite green runs from other commits. Fix the workflow (pull_request on any base, or push on codex/**) or rely on the landing plan collapsing the stack (auto-retarget). Adopt three standing rules: stack depth <=2; plan/spec narrowings land only as plan-branch commits, never inside the implementation PR measured against them; every lifecycle fix ships with an injected-failure test of the failure, not the fix. Holistic ledger #13 + section 4d: https://github.com/jlevy/metaproc/pull/37#issuecomment-5402647775
