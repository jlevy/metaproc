---
type: is
id: is-01m1fyjvnjysz23ax8tpad21a2
title: Implement native macOS and Linux safety providers
kind: task
status: in_progress
priority: 1
version: 7
spec_path: docs/project/specs/active/plan-2026-09-01-safeproc-local-incubation.md
labels: []
dependencies:
  - type: blocks
    target: is-01m1fyjw2enq5vq7qxthjfphqg
  - type: blocks
    target: is-01m1fyjweng4ryydmr4vqsvpa1
parent_id: is-01m1fxnwnyqvq1gg8ak7317kyc
created_at: 2026-09-02T02:18:48.881Z
updated_at: 2026-09-02T04:12:35.090Z
---
Linux provider implemented and tested (procfs, cgroup headroom, PSI three-state capability, swap-in rate, PSS behind the accuracy gate). The macOS provider is ported from memory_guard.py (host_statistics64, proc_pid_rusage, vm.swapusage, suspension distance, pressure alarm, thread priority) plus a new libproc process table, but was written on Linux and is NOT validated natively. Remaining work is the macOS handoff in packages/safeproc/docs/architecture.md: run make safeproc-test on macOS, verify every ctypes layout against the SDK headers (proc_bsdinfo is new), cross-check readings against vm_stat, footprint, sysctl, and ps, confirm harden_scheduling reaches priority 63, and run the live and launch tests for the kqueue path. Branch claude/safeproc-incubation.
