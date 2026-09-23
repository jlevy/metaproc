---
type: is
id: is-01kyx38gn4gwmp93rst4psbm0x
title: Release metaproc 0.2.0 with softschema 0.4
kind: task
status: closed
priority: 3
version: 7
labels:
  - release
dependencies: []
parent_id: is-01kyx37mj1agq5zha1x5gn574f
created_at: 2026-07-31T22:03:34.947Z
updated_at: 2026-08-01T05:41:57.725Z
closed_at: 2026-08-01T05:41:57.724Z
close_reason: null
---
After PR #3 and release-preparation PR #4 merge, create Metaproc v0.2.0 from the validated main commit using docs/releases/v0.2.0.md. Watch the Publish to PyPI workflow to completion; verify PyPI metadata and artifacts; then run the documented isolated uvx smoke tests. Versions remain tag-derived through uv-dynamic-versioning.

## Notes

Completed end to end. PR #3 merged at 849a8edab8ede3a46ba82ac38761abf7eb5b0b56; PR #4 merged at release commit c85c4e0ee81c9627a3ad233f8f0080b22d397b6f. GitHub release v0.2.0 and Publish to PyPI run 30684535816 attempt 2 passed through OIDC. PyPI wheel SHA-256 b47f67e1fc0c26b1ae2bda003c3aeaf37926686730d6528230ee81b71f8dd7c4 and sdist SHA-256 3ea8c591183cd3adbaccc2b1f9a98b1226d2ce7a40b7c1c50736c446ec55977b are non-yanked. Registry-only uvx help, skill, env-template, import/package-path, plugin, and portable-serialization checks pass. Attestations and CLI --version are separately tracked as mp-s901 and mp-43wa.
