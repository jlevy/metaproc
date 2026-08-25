---
type: is
id: is-01m0vjtjdxm7g8gkrxznmp5qd2
title: RunPool snapshot test must wait for observed activation
kind: bug
status: closed
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - testing
dependencies:
  - type: blocks
    target: is-01m0vhs620ptcvxv074ccx88z4
parent_id: is-01m0r92q2y1pe7dmhrcj6nst7q
created_at: 2026-08-25T04:28:30.013Z
updated_at: 2026-08-25T04:36:01.063Z
closed_at: 2026-08-25T04:36:01.062Z
close_reason: "Fixed on PR #37 head d5accf7. Snapshot activation uses a bounded observed-state wait and passed three consecutive focused runs. CLI smoke fixtures declare their tiny test disk budget while default preflight unit tests remain isolated. The unmodified full pre-push gate passed 4,356 tests with 8 skipped plus lint, type, docs, supply-chain, browser, distribution, and installed-wheel checks."
resolution: null
duplicate_of: null
---
The full pre-push suite exposed a deterministic timing defect under load: tests/test_runpool_pool.py::TestRunPool::test_snapshot sleeps 0.2s even though pool_config.monitor_interval_s is 0.5s, then requires active_count == 1. Replace the fixed sleep with a bounded observation of active launch state, keep the child alive long enough to inspect, and prove repeated focused plus full-suite stability. This is test hardening, not a production retry or admission change.
