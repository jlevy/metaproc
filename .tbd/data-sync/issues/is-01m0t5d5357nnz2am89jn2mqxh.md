---
type: is
id: is-01m0t5d5357nnz2am89jn2mqxh
title: "Follow up on PR #32 architecture review findings"
kind: task
status: in_progress
priority: 1
version: 3
labels:
  - pr-review
dependencies: []
created_at: 2026-08-24T15:14:44.452Z
updated_at: 2026-08-24T15:38:51.252Z
---
The #32 review (F1-F8) was posted at plan revision 7f2b022; the plan has since been updated to 4dcc683. Verify which findings the revision addresses, note remaining open items, and track disposition of blockers F3 (resource model) and F6 (cross-scope recovery) before mapped-scope implementation lands.

## Notes

Disposition verified 2026-08-24: plan revision 7f2b022→4dcc683 faithfully incorporates all F1-F8 findings (disposition table, resequenced decision summary, per-item force promoted to goal with qualified selectors, / item sigil, input-indirection roster, restored derived-subset trigger + barrier-drain metric, byte authority honestly scoped incl. ramp/warm-restore, gcp-worker+mapped-composite rejection, additive outcome schema, spec-loading layering, ports lower to clauses). Nit flagged in stack comment: Phase 1 checkboxes marked [x] for unmerged stacked work — annotate with PR numbers or check at merge time. REMAINING: F3/F6 implementation-level disposition rides on #33-#35 must-fixes (see mp-crvq/mp-1jjs/mp-nc0o); mapped scopes, byte authority, per-item force still Phase 2/3 open.
