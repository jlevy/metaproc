---
type: is
id: is-01m0vxbzj2w00ynmcp61qzqj48
title: "PR #37 B9: decide optional-default resume compatibility"
kind: task
status: closed
priority: 2
version: 5
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-25T07:32:46.274Z
updated_at: 2026-08-25T19:28:51.010Z
closed_at: 2026-08-25T19:28:51.010Z
close_reason: "Decision recorded and tested: resolved optional defaults are immutable under an existing run identity."
resolution: null
duplicate_of: null
---
Immutable resolved-variable resume rejects a changed optional input default. Decide whether that is intended identity semantics or needs a narrower comparison, then record a test-backed disposition. Source: PR #37 senior review B9.

## Notes

Fixed by decision: resolved optional defaults are immutable run identity. A changed default fails closed under an existing run ID; the behavior has direct regression coverage and changelog documentation.
