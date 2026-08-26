---
type: is
id: is-01m0t5d4rrqrb7b1ernp3kgaqk
title: "Overall stack-structure review of #32-#36"
kind: task
status: closed
priority: 1
version: 9
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
  - architecture
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
child_order_hints:
  - is-01m0t7zmhacz125fmx2mn99b2m
created_at: 2026-08-24T15:14:44.119Z
updated_at: 2026-08-25T19:28:30.034Z
closed_at: 2026-08-24T19:05:45.685Z
close_reason: "The reviewed stack was consolidated only at the auth boundary and is now #39 → #32 → #33 → #34 → #35 → #37; #36 is closed as superseded. Per-PR defects have explicit dispositions, and integration head c061cad passed exact five-job GitHub CI run 32765621039. Stack disposition published at issuecomment-5399988297."
resolution: null
duplicate_of: null
---
Review the stacking itself: bases and merge order (implementation PRs based on the draft plan branch), diff hygiene between rungs, alignment with the accepted review sequencing (#31 first, context before mapped scopes), scope coherence per rung, what remains unstacked (mapped scopes, admission, per-item force). Post as comment on the stack root #32. Note #19 as open but independent.

## Notes

HOLISTIC DOC posted 2026-08-24 on #37 (top of stack): https://github.com/jlevy/metaproc/pull/37#issuecomment-5402647775 — consolidated ledger (15 issues, ranked, with status), added/removed inventory, 4 root causes (scope identity not first-class; lifecycle hand-woven per site; run_process.py monolith; unverified stack + moving spec), and the landing plan: Wave 1 = #39 (+2 small fixes) → #19 → #38 (verified at 809fccc, exact-head CI green) → #32 (auto-retargets); Wave 2 = #33 → #34 → #35 one at a time rebased on main so CI runs, #35 gated on 3 injected-failure tests; Hold = #37 rebased, undraft on ScopeIdentity fix + terminal-state + item-key anchor, graph.py split out. End state: 1 draft + 1 small PR. #32-base-on-#39 confirmed deliberate (plan branch contains the guard commit). AUTHOR RESPONSES so far: #38 all 8 findings fixed and spot-verified; #37/#33/#34/#35/#39 round-2 unanswered.
