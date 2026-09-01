---
type: is
id: is-01m1dx1c2x091evxbembw1dnxn
title: Build standalone process-safety runtime and evaluate pool extraction
kind: feature
status: open
priority: 1
version: 5
spec_path: docs/project/specs/active/plan-2026-09-01-runpool-host-safety.md
labels: []
dependencies: []
created_at: 2026-09-01T07:13:18.428Z
updated_at: 2026-09-01T20:46:27.915Z
---

## Notes

Plan published in https://github.com/jlevy/metaproc/pull/62. Current recommendation: one cross-platform repository for the shared safety core, daemonless watch guard, broker/sentinel, owned-process Python API, and thin run/pool/status/replay CLI surfaces. Metaproc should build on SafeProcess. Full SafeRunPool extraction is conditional on a Phase 0 vertical-slice gate; if it fails, Metaproc retains its one scheduler and uses SafeProcess directly. Keep open for phased implementation.
