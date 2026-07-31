---
type: is
id: is-01kyx38gn4gwmp93rst4psbm0x
title: Release metaproc 0.2.0 with softschema 0.3
kind: task
status: open
priority: 3
version: 2
labels:
  - release
dependencies: []
parent_id: is-01kyx37mj1agq5zha1x5gn574f
created_at: 2026-07-31T22:03:34.947Z
updated_at: 2026-07-31T22:27:50.317Z
---
Cut a release once the softschema 0.3 branch merges, so downstream consumers pin a published version instead of a git branch. Breaking changes (required --contract, renamed structure-report contract id) mean a 0.x minor bump: 0.1.0 -> 0.2.0. Move the CHANGELOG Unreleased section under the new heading and tag; version is derived from the git tag via uv-dynamic-versioning.
