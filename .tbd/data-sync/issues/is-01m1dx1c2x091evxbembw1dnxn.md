---
type: is
id: is-01m1dx1c2x091evxbembw1dnxn
title: Build standalone process-safety runtime and evaluate pool extraction
kind: feature
status: open
priority: 1
version: 6
spec_path: docs/project/specs/active/plan-2026-09-01-runpool-host-safety.md
labels: []
dependencies: []
created_at: 2026-09-01T07:13:18.428Z
updated_at: 2026-09-01T21:03:30.777Z
---

## Notes

Plan published in https://github.com/jlevy/metaproc/pull/62. Current recommendation: one cross-platform repository for the shared safety core, daemonless watch guard, broker/sentinel, owned-process Python API, and thin run/pool/status/replay CLI surfaces. Metaproc should build on SafeProcess. Full SafeRunPool extraction is conditional on a Phase 0 vertical-slice gate; if it fails, Metaproc retains its one scheduler and uses SafeProcess directly. Procguard v1.5.1 was checked out under ignored attic/ and reviewed statically. It is a useful macOS owned-process and failure-corpus reference, not a dependency candidate: its ordinary posix_spawn/process-group path, kqueue loop, clock split, result schema, and race tests inform the plan; its root-PID-only accounting, fork-on-resource-limit path, global signal singleton, incomplete error cleanup, macOS-only backend, and orchestration feature growth become explicit constraints and tests. Keep open for phased implementation.
