---
type: is
id: is-01m1fyjw2enq5vq7qxthjfphqg
title: Ship brokerless ProcessMonitor, watch, and replay
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
created_at: 2026-09-02T02:18:49.293Z
updated_at: 2026-09-02T02:18:50.079Z
---
Implement ProcessTarget, ProcessMonitor, MonitoredProcess, passive-by-default safeproc watch, and offline replay without broker or Metaproc imports; prove PID reuse, exit, sleep, sampling starvation, compression, PSI, and outside-tree behavior.
