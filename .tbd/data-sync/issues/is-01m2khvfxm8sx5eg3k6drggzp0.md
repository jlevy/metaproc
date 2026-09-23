---
type: is
id: is-01m2khvfxm8sx5eg3k6drggzp0
title: "PR #79 review R5: run-parallel must keep ExecutionFailure classification instead of re-classifying the clipped error"
kind: bug
status: closed
priority: 1
version: 2
labels: []
dependencies: []
parent_id: is-01m2khtp39hxxwknrtk7gzqpfs
created_at: 2026-09-15T22:08:59.827Z
updated_at: 2026-09-15T22:56:22.157Z
closed_at: 2026-09-15T22:56:22.126Z
close_reason: "Fixed in f3bae15: ExecutionFailure carries verdict; run-parallel uses failure.verdict/failure_class. Test: test_run_parallel_retry_policy_uses_the_unclipped_path_free_classification."
resolution: null
duplicate_of: null
---
PR #79 (https://github.com/jlevy/metaproc/pull/79#issuecomment-5688716379), High. src/metaproc/commands/run_parallel.py:970-999 and :1016 keep only ExecutionFailure.error and rerun classify_error/classify_failure on the clipped, log-path-bearing string. Step ids or item keys containing permanent words (quota, billing, cancelled) permanently fail transient errors; diagnostics longer than 12 lines lose the 429 line and the retry; attempt-id nanosecond fields containing 429/503 cause spurious retries. Fix: add verdict: RetryVerdict to ExecutionFailure computed on the same pre-clip string; use failure.verdict and failure.failure_class in the sequential loop. Regression: run-parallel parametrized on step id quota-check and a >12-line diagnostic.
