---
type: is
id: is-01m0typw2vc7ze7estjdd916nn
title: "PR #38 review 3: resolve semantic conflict with stacked PR #37"
kind: bug
status: open
priority: 1
version: 4
labels: []
dependencies: []
parent_id: is-01m0typ5swnqc9v7gee2ymkjs9
created_at: 2026-08-24T22:36:57.306Z
updated_at: 2026-08-25T03:16:56.399Z
---
Review finding 3 at issuecomment-5402359572. PR #37 and PR #38 edit run_process.py and test_run_config.py in opposite directions around _normalize_filestore_runs_path. Record the intended no-workstation-alias decision and ensure PR #37 is deliberately rebased rather than mechanically resolving the conflict.

## Notes

Deferred disposition: PR #38 landed first and merged at c4abc07 on 2026-08-24 after reviewed head 809fccc passed exact-head CI. The gate is now actionable but remains open: PR #37 must deliberately rebase, preserve immutable-variable resume validation, and keep the no-workstation-alias decision. Cross-PR notice: https://github.com/jlevy/metaproc/pull/37#issuecomment-5402608943
