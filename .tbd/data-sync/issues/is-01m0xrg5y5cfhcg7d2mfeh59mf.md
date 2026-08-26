---
type: is
id: is-01m0xrg5y5cfhcg7d2mfeh59mf
title: "PR #48: remove downstream-internal leakage from public Metaproc surfaces"
kind: bug
status: closed
priority: 0
version: 6
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels: []
dependencies:
  - type: blocks
    target: is-01m0xrg6jeywxa1hwns3eay01m
parent_id: is-01m0xrg4vr6n4znzxz0kkxxxt7
created_at: 2026-08-26T00:46:09.861Z
updated_at: 2026-08-26T01:41:23.575Z
closed_at: 2026-08-26T01:41:23.572Z
close_reason: All mutable public surfaces and remote refs are clean; immutable merged history was preserved.
resolution: null
duplicate_of: null
---
Audit branch files, reachable Git history, PR body, reviews, inline comments, issue comments, linked documents, and related historical PR comments for private downstream repository names, run identities, issue IDs, paths, or operational details. Delete or rewrite leakage without copying it into new public comments.

## Notes

Completed the public cleanup: repository content and generated env docs are neutralized; affected PR bodies, formal reviews, issue comments, and inline comments were rewritten or deleted; eight leaked remote branch refs belonging only to closed superseded PRs were deleted. Final bounded scans of all PR bodies/comments/reviews, fully paginated issue and inline comments, current repository content, and remote refs are clean. Reachable immutable main history was not rewritten.
