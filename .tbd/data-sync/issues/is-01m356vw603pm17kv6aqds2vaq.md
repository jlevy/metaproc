---
type: is
id: is-01m356vw603pm17kv6aqds2vaq
title: Investigate pytest worker termination in full scheduler scale test
kind: bug
status: open
priority: 2
version: 3
labels: []
dependencies: []
created_at: 2026-09-22T18:43:17.821Z
updated_at: 2026-09-22T18:56:47.246Z
---
During PR 92 verification on macOS/Python 3.14.7, make verify passed with 5069 tests; the required pre-push rerun with -n logical then lost xdist worker gw2 during tests/execution_model/test_scale.py::TestEnvelope::test_a_full_chain_drains_in_reasonable_time (5068 passed, 34 skipped, worker terminated without assertion or traceback). Execution-model source and tests are unchanged in this PR. Investigate timeout/resource contention or another termination cause without weakening assertions or timeouts. Isolated rerun and bounded-worker full verification pending.

## Notes

Initial full make verify passed with 5069 tests. A later -n logical pre-push lost gw2 during unchanged scheduler scale test without traceback. Isolated -n0 test passed in 112.00s. Host snapshot showed load averages 191.85/144.17/103.78 on 10 logical CPUs with independent test workloads. Complete pre-push make verify then passed on committed e908f70 with PYTEST_ARGS=-n 2: 5069 passed, 34 skipped in 458.48s; all audits and installed-wheel checks passed. No assertions, timeouts, tests, or tracked configuration changed. Investigate hard timing gates on shared hosts; exact worker-exit cause remains unreported.
