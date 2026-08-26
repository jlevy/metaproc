---
type: is
id: is-01m0t5d5357nnz2am89jn2mqxh
title: "Follow up on PR #32 architecture review findings"
kind: task
status: closed
priority: 1
version: 7
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-24T15:14:44.452Z
updated_at: 2026-08-25T19:28:30.034Z
closed_at: 2026-08-24T19:05:45.244Z
close_reason: "The revised plan at f6d7214 preserves the full F1-F8 architecture disposition and fixes the stack-review checkbox nit by naming the unmerged PR for each completed implementation item. Remaining F3/F6 implementation proof stays open under mp-0cyw/mp-0ukj/mp-rrfn. Disposition published on PR #32 at issuecomment-5399988297."
resolution: null
duplicate_of: null
---
The #32 review (F1-F8) was posted at plan revision 7f2b022; the plan has since been updated to 4dcc683. Verify which findings the revision addresses, note remaining open items, and track disposition of blockers F3 (resource model) and F6 (cross-scope recovery) before mapped-scope implementation lands.

## Notes

ROUND 2 (2026-08-24, plan f6d7214): https://github.com/jlevy/metaproc/pull/32#issuecomment-5402357988 — F1-F8 disposition faithful; per-slice PR annotations added; escalation triggers repaired. BUT plan was SCOPED DOWN after review: named process ports deleted (parent re-declares child's resolved path — re-opens A4 as YAML duplication); dict(parent_variables) leak RETAINED as 'hardening'; evidence pointer dropped; executor 'explicit ceiling' → 'independent of leaf ceiling'. Namespace deferral is NOT ergonomic — it causes #37 B1 (all mapped items share parent run.dir). Process ask filed: plan narrowings must land on the plan branch, not inside the implementation PR being measured.
