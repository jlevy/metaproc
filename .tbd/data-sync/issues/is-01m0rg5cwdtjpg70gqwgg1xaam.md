---
type: is
id: is-01m0rg5cwdtjpg70gqwgg1xaam
title: Make gcp run install shipped workspace packages safely
kind: task
status: closed
priority: 1
version: 3
labels: []
dependencies: []
created_at: 2026-08-23T23:44:15.756Z
updated_at: 2026-08-23T23:59:48.416Z
closed_at: 2026-08-23T23:59:48.415Z
close_reason: Implemented repeatable workspace-package installation, nested uv environment pinning, vendored-submodule archive exclusion, docs, and regression coverage; PR 30 CI is green.
resolution: null
duplicate_of: null
---
Let generic GCP Batch jobs install selected packages from a shipped workspace into the image environment, keep nested uv invocations on that environment, and exclude the vendored Metaproc submodule from default workspace archives.
