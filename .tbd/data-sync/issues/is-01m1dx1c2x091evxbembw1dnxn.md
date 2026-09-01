---
type: is
id: is-01m1dx1c2x091evxbembw1dnxn
title: Extract safe subprocess pool and integrate Metaproc
kind: feature
status: open
priority: 1
version: 4
spec_path: docs/project/specs/active/plan-2026-09-01-runpool-host-safety.md
labels: []
dependencies: []
created_at: 2026-09-01T07:13:18.428Z
updated_at: 2026-09-01T07:40:49.463Z
---

## Notes

Design plan published in https://github.com/jlevy/metaproc/pull/62. Revised to recommend a cross-platform safe subprocess-pool package: in-memory pool library, policy library, per-user broker/sentinel, and standalone CLI. It replaces Metaproc's generic RunPool core; durable workflow and retry semantics stay in Metaproc. Keep open for phased implementation.
