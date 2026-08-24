---
type: is
id: is-01m0s7cxknep5425wm4z90bcqj
title: Type and catalog retry-later checkpoint artifacts
kind: task
status: open
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - authentication
  - execution-model
dependencies: []
parent_id: is-01m0s0r624c0eszrgnq4qgjjbe
created_at: 2026-08-24T06:30:19.509Z
updated_at: 2026-08-24T07:23:24.666Z
---
retry_later.yaml is JSON emitted under a YAML suffix, is absent from metaproc.paths and the artifact catalog, and uses unvalidated dataclasses at a durable boundary. Align it with the repository YAML/Pydantic/schema-token conventions while retaining compatibility with existing version-1 checkpoints.

## Notes

Reuse and harden the existing retry_later checkpoint/event/deferred/resume-daemon stack from the earlier Phase 2c work (trading-b2bd and trading-0s98). Preserve version-1 compatibility; do not introduce a second checkpoint protocol or resume service.
