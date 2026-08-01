---
type: is
id: is-01kyx38gn4gwmp93rst4psbm0x
title: Release metaproc 0.2.0 with softschema 0.4
kind: task
status: in_progress
priority: 3
version: 5
labels:
  - release
dependencies: []
parent_id: is-01kyx37mj1agq5zha1x5gn574f
created_at: 2026-07-31T22:03:34.947Z
updated_at: 2026-08-01T04:40:51.906Z
---
After PR #3 and release-preparation PR #4 merge, create Metaproc v0.2.0 from the validated main commit using docs/releases/v0.2.0.md. Watch the Publish to PyPI workflow to completion; verify PyPI metadata and artifacts; then run the documented isolated uvx smoke tests. Versions remain tag-derived through uv-dynamic-versioning.

## Notes

PR #3 merged at 849a8ed. Release execution started: retarget PR #4 to main, validate exact release commit, merge, publish v0.2.0, and verify PyPI/uvx end to end.
