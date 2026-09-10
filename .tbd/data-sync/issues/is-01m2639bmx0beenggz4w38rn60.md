---
type: is
id: is-01m2639bmx0beenggz4w38rn60
title: "PR 69: consolidate model support and prevent explicit model substitution"
kind: task
status: closed
priority: 1
version: 6
spec_path: docs/project/specs/active/plan-2026-09-10-model-catalog-followups.md
labels: []
dependencies: []
parent_id: is-01m26342tjmj8qh22599bhd3sn
created_at: 2026-09-10T16:44:17.948Z
updated_at: 2026-09-10T18:45:36.280Z
closed_at: 2026-09-10T17:50:42.610Z
close_reason: Implemented, documented, and independently cross-checked; make verify passed with 4673 tests, 8 skips, clean audits, and installed-wheel smoke. Recurring review is active. Publishing and merge tracked separately.
resolution: null
duplicate_of: null
---
Review all four adapter validation and command paths. Consolidate time-sensitive model names and verified support metadata, cover every changed adapter with real command-building regressions, and distinguish catalog knowledge from backend/account availability.

## Notes

Implemented shared dated catalog, derived Pi IDs, current verified provider entries, exact Codex effort syntax, fail-closed explicit selection including falsey values, and structured auth-check failure without inference. All prior accepted IDs/defaults retained. Sol cross-check findings corrected. Final gate: 4673 tests passed, 8 skipped; audits clean; distribution checks finishing. Review comments: https://github.com/jlevy/metaproc/pull/69#issuecomment-5622346101 and https://github.com/jlevy/metaproc/pull/69#issuecomment-5622911202
