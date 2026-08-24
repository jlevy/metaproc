---
type: is
id: is-01m0t5d4rrqrb7b1ernp3kgaqk
title: "Overall stack-structure review of #32-#36"
kind: task
status: closed
priority: 1
version: 7
labels:
  - pr-review
  - architecture
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
child_order_hints:
  - is-01m0t7zmhacz125fmx2mn99b2m
created_at: 2026-08-24T15:14:44.119Z
updated_at: 2026-08-24T22:32:05.755Z
closed_at: 2026-08-24T19:05:45.685Z
close_reason: "The reviewed stack was consolidated only at the auth boundary and is now #39 → #32 → #33 → #34 → #35 → #37; #36 is closed as superseded. Per-PR defects have explicit dispositions, and integration head c061cad passed exact five-job GitHub CI run 32765621039. Stack disposition published at issuecomment-5399988297."
resolution: null
duplicate_of: null
---
Review the stacking itself: bases and merge order (implementation PRs based on the draft plan branch), diff hygiene between rungs, alignment with the accepted review sequencing (#31 first, context before mapped scopes), scope coherence per rung, what remains unstacked (mapped scopes, admission, per-item force). Post as comment on the stack root #32. Note #19 as open but independent.

## Notes

ROUND 2 (2026-08-24): stack now main → #39 → #32 → #33 → #34 → #35 → #37 (clean 6-deep linear chain). #36 CLOSED as superseded. #38 independent and SEMANTICALLY CONFLICTS with #37: 6 merge-tree conflicts, opposite decisions on _normalize_filestore_runs_path, same test body edited both ways — needs intent decision. #39 is the stack base so its weakened complexity guard is inherited by every rung. Recommended order: #39 → #38 → rebase stack → #33 → #34 → #35 → #37. #19 independent, mergeable today. PATTERN across both rounds: remediation commits carry ~as many new lifecycle hazards as the slices they repair; tests written against the fix, not the failure. Filed: per-invariant injected-failure tests for Phase 2/3.
