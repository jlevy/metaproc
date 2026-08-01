---
type: is
id: is-01kyx38gn4gwmp93rst4psbm0x
title: Release metaproc 0.2.0 with softschema 0.4
kind: task
status: open
priority: 3
version: 3
labels:
  - release
dependencies: []
parent_id: is-01kyx37mj1agq5zha1x5gn574f
created_at: 2026-07-31T22:03:34.947Z
updated_at: 2026-08-01T04:05:45.226Z
---
After PR #3 merges, cut Metaproc 0.2.0 with the complete SoftSchema 0.2-0.4 and frontmatter-format 0.4 migration. Move the CHANGELOG Unreleased section under the new heading and tag; the version is derived from the git tag via uv-dynamic-versioning. The hard compatibility cuts remain the required --contract option, the structure-report contract-id rename, portable/offline YAML validation, and deterministic alias-free mapping writes.

## Notes

Do not release from the review branch. Implementation and end-to-end PR delivery are tracked by mp-9u9s; this bead starts after PR #3 merges.
