---
type: is
id: is-01m4nkmdjjhsaksdk891gmq5p7
title: Freeze rate-limit burst cuts in run pool manual mode
kind: bug
status: open
priority: 3
version: 2
labels: []
dependencies: []
created_at: 2026-10-11T13:49:57.712Z
updated_at: 2026-10-11T14:27:48.512Z
---
Found while addressing PR #106 review B1 (https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5482373521). ScaleOverride documents mode: manual as freezing both governors, and the manual monitor tick skips _adjust_concurrency, which owns provider recovery. PR #106 made the quota governor log action ignored in manual mode, but _apply_rate_limit_backoff (src/metaproc/runpool/pool.py) still lowers _provider_ceiling on a 429 burst in manual mode, and nothing restores it while the mode lasts, so an operator-frozen cap can silently halve. Pre-existing; fix the same way (record the 429 for status, skip the cut in manual mode) with a test that manual mode plus a burst leaves capacity at the operator cap.

## Notes

Reach widened by PR #106 (review C2, https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5483637641): scalar agent steps now report every classified failure to the run pool (run_process.py _report_scalar_failure), so a run of scalar leaves, which could not trigger the burst governor before, can now cut an operator-frozen manual-mode cap on three 429s in 30 s. Reviewer probe: mode manual, operator_cap 20, three rate_limited reports in 30 s took capacity 20 -> 10 and it stayed at 10 across ten ticks. Take it in the next change to pool.py. Fix: in _apply_rate_limit_backoff, when _operator_mode_override is manual, record the sample and return without touching _provider_ceiling; test that manual mode plus a burst leaves capacity at the operator cap; and qualify ScaleOverride's 'freezes both governors' wording until it holds. Waits on nothing.
