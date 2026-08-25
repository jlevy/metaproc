---
type: is
id: is-01m0typw2vc7ze7estjdd916nn
title: "PR #38 review 3: resolve semantic conflict with stacked PR #37"
kind: bug
status: closed
priority: 1
version: 7
labels: []
dependencies:
  - type: blocks
    target: is-01m0vhs620ptcvxv074ccx88z4
parent_id: is-01m0typ5swnqc9v7gee2ymkjs9
created_at: 2026-08-24T22:36:57.306Z
updated_at: 2026-08-25T13:19:02.294Z
closed_at: 2026-08-25T13:19:02.294Z
close_reason: "Re-verified addressed at PR #37 head b5c4721: scope identity/containment and exception/concurrency fixes are present, required-edge graph work is split into the base, and the branch descends from post-#38 main without restoring workstation aliasing. Full and pre-push verification passed."
resolution: null
duplicate_of: null
---
Review finding 3 at issuecomment-5402359572. PR #37 and PR #38 edit run_process.py and test_run_config.py in opposite directions around _normalize_filestore_runs_path. Record the intended no-workstation-alias decision and ensure PR #37 is deliberately rebased rather than mechanically resolving the conflict.

## Notes

Deferred disposition: PR #38 landed first. It reached final head c4abc07, passed all five exact-head CI jobs in run 32804085015, and merged to main at 6ac9c65 on 2026-08-24. The gate is now actionable but remains open: PR #37 must deliberately rebase, preserve immutable-variable resume validation, and keep the no-workstation-alias decision. Cross-PR notice: https://github.com/jlevy/metaproc/pull/37#issuecomment-5402608943
