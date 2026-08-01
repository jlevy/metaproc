---
type: is
id: is-01kyx38gn4gwmp93rst4psbm0x
title: Release metaproc 0.2.0 with softschema 0.4
kind: task
status: open
priority: 3
version: 4
labels:
  - release
dependencies: []
parent_id: is-01kyx37mj1agq5zha1x5gn574f
created_at: 2026-07-31T22:03:34.947Z
updated_at: 2026-08-01T04:36:08.537Z
---
After PR #3 and release-preparation PR #4 merge, create Metaproc v0.2.0 from the validated main commit using docs/releases/v0.2.0.md. Watch the Publish to PyPI workflow to completion; verify PyPI metadata and artifacts; then run the documented isolated uvx smoke tests. Versions remain tag-derived through uv-dynamic-versioning.

## Notes

Release preparation is complete in PR #4 at f72d414. No v0.1.0 tag, GitHub release, or PyPI distribution exists, so v0.2.0 is the first public release. Do not release from either review branch. PR #4 is stacked on PR #3, has full local make verify evidence, an exact v0.2.0 tag-simulation build, and green hosted CI run 30684107078.
