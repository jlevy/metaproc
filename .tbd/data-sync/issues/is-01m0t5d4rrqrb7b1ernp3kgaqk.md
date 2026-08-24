---
type: is
id: is-01m0t5d4rrqrb7b1ernp3kgaqk
title: "Overall stack-structure review of #32-#36"
kind: task
status: in_progress
priority: 1
version: 3
labels:
  - pr-review
  - architecture
dependencies: []
created_at: 2026-08-24T15:14:44.119Z
updated_at: 2026-08-24T15:38:50.804Z
---
Review the stacking itself: bases and merge order (implementation PRs based on the draft plan branch), diff hygiene between rungs, alignment with the accepted review sequencing (#31 first, context before mapped scopes), scope coherence per rung, what remains unstacked (mapped scopes, admission, per-item force). Post as comment on the stack root #32. Note #19 as open but independent.

## Notes

Stack review posted 2026-08-24 on #32: https://github.com/jlevy/metaproc/pull/32#issuecomment-5397585780 — verdict: stack structure sound (true linear commit stack, one commit per rung, plan-branch gating, declared boundaries, sequencing matches accepted review order). Cross-cutting theme: serious defects sit at the exact seams being fixed (identity binding #34, env composition #35, cancellation reachability #33/#35, test falsifiability #33) — 'seam characterization tests land with the seam' is the rule for the mapped-scope slice. Merge order: #32 (with checkbox-annotation nit) → #33 → #34 → #35 after respective must-fixes; #36 stays draft until mp-tibt convergence. #19 independent, decide separately. FOLLOW UP: confirm merge lands in this order with fixes.
