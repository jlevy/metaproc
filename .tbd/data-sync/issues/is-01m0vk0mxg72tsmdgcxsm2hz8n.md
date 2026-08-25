---
type: is
id: is-01m0vk0mxg72tsmdgcxsm2hz8n
title: CLI smoke fixtures must not depend on host free disk
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
created_at: 2026-08-25T04:31:49.167Z
updated_at: 2026-08-25T04:36:01.072Z
closed_at: 2026-08-25T04:36:01.072Z
close_reason: "Fixed on PR #37 head d5accf7. Snapshot activation uses a bounded observed-state wait and passed three consecutive focused runs. CLI smoke fixtures declare their tiny test disk budget while default preflight unit tests remain isolated. The unmodified full pre-push gate passed 4,356 tests with 8 skipped plus lint, type, docs, supply-chain, browser, distribution, and installed-wheel checks."
resolution: null
duplicate_of: null
---
Metaproc's full suite fails on a correctly functioning low-disk development host because three CLI smoke fixtures exercise actual preflight without declaring their tiny test disk budget. Set METAPROC_PREFLIGHT_MIN_DISK_GB only inside those fixtures, keep tests/test_preflight.py isolated so default and batch-budget semantics remain covered, and prove the full suite passes without a global environment override.
