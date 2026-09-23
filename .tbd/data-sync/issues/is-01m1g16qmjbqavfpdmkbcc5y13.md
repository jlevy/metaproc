---
type: is
id: is-01m1g16qmjbqavfpdmkbcc5y13
title: Linux calibration soak for Safeproc defaults on a dedicated host
kind: task
status: open
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-09-01-safeproc-local-incubation.md
labels: []
dependencies:
  - type: blocks
    target: is-01m1fyjwv0ng2tpcnbey5vcxxc
parent_id: is-01m1fxnwnyqvq1gg8ak7317kyc
created_at: 2026-09-02T03:04:37.266Z
updated_at: 2026-09-02T03:04:39.039Z
---
Measure reserve fraction, PSI some/full thresholds, MemAvailable slope, swap-in rate (pswpin), PSS sampling cost, and settle windows on a dedicated Linux host, with and without a memory-limited cgroup, before Linux defaults ship. Produce a replay corpus parallel to the macOS guard corpus. Origin: review F4 of pull request 62. Blocks the Linux defaults in mp-c225.
