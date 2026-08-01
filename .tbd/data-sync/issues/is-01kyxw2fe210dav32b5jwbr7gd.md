---
type: is
id: is-01kyxw2fe210dav32b5jwbr7gd
title: Expose metaproc CLI version output
kind: task
status: open
priority: 3
version: 1
spec_path: docs/releases/v0.2.0.md
labels:
  - cli
  - release
dependencies: []
parent_id: is-01kyx37mj1agq5zha1x5gn574f
created_at: 2026-08-01T05:17:11.490Z
updated_at: 2026-08-01T05:17:11.490Z
---
The isolated 0.2.0 smoke verified importlib.metadata.version("metaproc") == "0.2.0", but metaproc --version exits 2 because the Typer root command has no version option. Add a dynamically derived CLI version option, cover its exit/output contract, and include it in the next patch release. This is not a runtime cutover blocker because the installed distribution metadata and package path are correct.
