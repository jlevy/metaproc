---
type: is
id: is-01m0t5d5357nnz2am89jn2mqxh
title: "Follow up on PR #32 architecture review findings"
kind: task
status: in_progress
priority: 1
version: 4
labels:
  - pr-review
dependencies: []
created_at: 2026-08-24T15:14:44.452Z
updated_at: 2026-08-24T22:31:54.280Z
---
The #32 review (F1-F8) was posted at plan revision 7f2b022; the plan has since been updated to 4dcc683. Verify which findings the revision addresses, note remaining open items, and track disposition of blockers F3 (resource model) and F6 (cross-scope recovery) before mapped-scope implementation lands.

## Notes

ROUND 2 (2026-08-24, plan f6d7214): https://github.com/jlevy/metaproc/pull/32#issuecomment-5402357988 — F1-F8 disposition faithful; per-slice PR annotations added; escalation triggers repaired. BUT plan was SCOPED DOWN after review: named process ports deleted (parent re-declares child's resolved path — re-opens A4 as YAML duplication); dict(parent_variables) leak RETAINED as 'hardening'; evidence pointer dropped; executor 'explicit ceiling' → 'independent of leaf ceiling'. Namespace deferral is NOT ergonomic — it causes #37 B1 (all mapped items share parent run.dir). Process ask filed: plan narrowings must land on the plan branch, not inside the implementation PR being measured.
