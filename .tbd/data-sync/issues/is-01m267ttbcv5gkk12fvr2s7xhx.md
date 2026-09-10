---
type: is
id: is-01m267ttbcv5gkk12fvr2s7xhx
title: "Post-merge CI: normalize shipped-document commit dates across time zones"
kind: bug
status: closed
priority: 1
version: 10
spec_path: docs/project/specs/active/plan-2026-09-10-model-catalog-followups.md
labels: []
dependencies: []
parent_id: is-01m26342tjmj8qh22599bhd3sn
created_at: 2026-09-10T18:03:44.363Z
updated_at: 2026-09-10T18:45:40.514Z
closed_at: 2026-09-10T18:27:21.536Z
close_reason: Completed and merged in PRs 69 and 76; reviewed commits, mandatory make verify gates, PR checks, and main CI all passed. Main is a125569a4992bd44e3ba7b924af987ba5ac2a4cf; https://github.com/jlevy/metaproc/actions/runs/34514137842.
resolution: null
duplicate_of: null
---
PR 69 exact tree passed all PR checks, but squash merge 850525b preserved the contributor timezone +0800, yielding author date 2026-09-11 while the commit instant and changed manuals are 2026-09-10 UTC. devtools.check_doc_dates uses unnormalized %ad --date=short and falsely fails main lint and repository tests. Normalize the date basis, document it, add real-git timezone regressions, verify the identical merged source tree passes, and land a narrow follow-up with CI.

## Notes

Fixed in PR https://github.com/jlevy/metaproc/pull/76, merged as a125569a4992bd44e3ba7b924af987ba5ac2a4cf. Reviewed source tree matches the merged tree. Full make verify and mandatory pre-push gate passed: 4675 tests, 8 skipped, clean audits, distribution inspection, installed-wheel smoke. Both real-Git timezone regressions and independent Sol cross-check passed. All five PR checks passed at 29c378e; all five main checks passed at a125569: https://github.com/jlevy/metaproc/actions/runs/34514137842. Final review: https://github.com/jlevy/metaproc/pull/76#issuecomment-5623445058.
