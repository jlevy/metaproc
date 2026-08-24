---
type: is
id: is-01m0t5d345y4pdjcjpepb9h4q6
title: Senior engineering review of the GTIA v3 PR stack (#32-#39)
kind: epic
status: open
priority: 1
version: 7
labels:
  - pr-review
  - architecture
dependencies: []
child_order_hints:
  - is-01m0tybsbnry4c6kqgcf7yb41s
  - is-01m0tybspk9mmjecddzgxz75ke
  - is-01m0tybt1wpr25671rtk5bstyr
  - is-01m0tybtcsprrm4rt4jm56c9dd
created_at: 2026-08-24T15:14:42.436Z
updated_at: 2026-08-24T22:32:03.093Z
---
Track senior reviews of the open stacked PRs: #32 (mapped-composite plan, review posted, updated since), #33 (shared run context), #34 (scalar auth pooling), #35 (lifecycle/cancellation), #36 (retry-later transport, draft). Includes an overall stack-structure review (ordering, bases, revertibility) and follow-up on whether posted findings are addressed before merge.

## Notes

ROUND 2 complete 2026-08-24. Comments posted on all 8 open PRs (#19/#32/#33/#34/#35/#37/#38/#39); #36 closed as superseded. Round-1 blockers all genuinely fixed and verified first-hand. Remaining must-fix: #37 B1 (mapped items share parent run.dir — cross-item contamination), #37 B2/B3 (sibling abandonment + unbounded scope concurrency), #38 status false-pass, #35 N1/N2/F2 (lease leak on pool kill, unbounded shutdown, _active finally), #33 N1 (undocumented executor ceiling), #34 B1/Finding-3-retry-case, #39 two-sided assertion. Cross-cutting: remediation commits keep introducing hazards at the seam being fixed; #38 vs #37 needs an intent decision.
