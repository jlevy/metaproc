---
type: is
id: is-01m1dx1c2x091evxbembw1dnxn
title: Build standalone process-safety runtime and evaluate pool extraction
kind: feature
status: in_progress
priority: 1
version: 8
spec_path: docs/project/specs/active/plan-2026-09-01-runpool-host-safety.md
labels: []
dependencies: []
created_at: 2026-09-01T07:13:18.428Z
updated_at: 2026-09-01T21:41:01.528Z
---

## Notes

Plan published in https://github.com/jlevy/metaproc/pull/62. Current recommendation: one cross-platform repository for a shared process-safety core with two first-class, non-substitutable contracts. SafeProcess owns a launch and establishes admission, isolated process-group identity, containment, and cleanup before target execution. AttachedProcess attaches to an existing PID/create-time-fenced tree, remains brokerless-capable, and observes and journals by default; signalling requires explicit authority and descendant revalidation. Both modes share host telemetry, pressure classification, policy, journals, replay, and platform backends. Thin run, watch, status, and replay CLIs are sound adapters over the Python library. A finite pool is acceptable, but full SafeRunPool extraction remains conditional on a Phase 0 vertical-slice gate; if it fails, Metaproc retains its one scheduler and uses SafeProcess directly. Procguard v1.5.1 was checked out under ignored attic/ and reviewed statically. It is a useful macOS owned-process and failure-corpus reference, not a dependency candidate: its ordinary posix_spawn/process-group path, kqueue loop, clock split, result schema, and race tests inform the plan; its root-PID-only accounting, fork-on-resource-limit path, global signal singleton, incomplete error cleanup, macOS-only backend, and orchestration feature growth become explicit constraints and tests. Keep open for phased implementation.
