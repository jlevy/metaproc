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
updated_at: 2026-08-25T03:13:30.167Z
---
Review finding 3 at issuecomment-5402359572. PR #37 and PR #38 edit run_process.py and test_run_config.py in opposite directions around _normalize_filestore_runs_path. Record the intended no-workstation-alias decision and ensure PR #37 is deliberately rebased rather than mechanically resolving the conflict.

## Notes

GATE IS NOW LIVE. PR #38 merged as 6ac9c65 on 2026-08-25, so the 'land #38 first' half of the deferred disposition is done. The remaining obligation is entirely on PR #37.

When #37 rebases onto current main it will conflict in src/metaproc/commands/run_process.py and tests/test_run_config.py, and a mechanical resolution WILL silently undo one side. #37 introduces _normalize_filestore_runs_path, which keeps and generalizes substring alias matching and extends it to _resume_variable_identity; #38 deleted exactly that behaviour. The same test body is edited in opposite directions — #38 wraps it in pytest.raises(CLIError, match='Resume mismatch.*run_dir'), #37 expects success with a changed _validate_run_config signature.

Required resolution: delete the workstation-alias branch from _normalize_filestore_runs_path deliberately, preserve #37's immutable-variable resume validation, and keep the no-workstation-alias decision from #38. Taking 'theirs' resurrects the alias; taking 'ours' breaks #37's variable-identity check.

Cross-PR notice already posted: https://github.com/jlevy/metaproc/pull/37#issuecomment-5402608943
