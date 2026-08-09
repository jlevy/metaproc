---
type: is
id: is-01kz3jq3zgt9hxkz9mpw032xx5
title: "PR #10 review R2: complete legacy refresh artifacts"
kind: bug
status: closed
priority: 1
version: 5
spec_path: docs/project/specs/done/plan-2026-08-03-focused-resource-observability.md
labels:
  - pr-10
  - review
dependencies: []
parent_id: is-01kz3j0k3j53xk0j70vqqkzqw6
created_at: 2026-08-03T10:29:08.719Z
updated_at: 2026-08-09T18:56:53.729Z
closed_at: 2026-08-03T10:40:42.118Z
close_reason: "R2 fixed in 5a9ac20: inactive legacy reports now emit the complete finalized artifact set, the regression test proves freshness is satisfied, clean-room make verify passed (3919 tests), renewed CI and Bugbot passed, and the inline thread is resolved with a disposition reply."
---
Cursor review thread PRRT_kwDOTeh_X86V77Fb at src/metaproc/commands/resource_report.py:156: legacy --refresh writes resources.json and the ledger but omits resource-usage-summary.md and its compiled schema sidecar, so freshness checks remain permanently unsatisfied. Add a regression test and make the existing legacy build path emit the complete reporting artifact set.
