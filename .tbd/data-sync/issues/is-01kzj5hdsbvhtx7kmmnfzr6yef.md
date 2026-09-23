---
type: is
id: is-01kzj5hdsbvhtx7kmmnfzr6yef
title: Register the resources.json document contract
kind: task
status: closed
priority: 2
version: 3
labels: []
dependencies: []
created_at: 2026-08-09T02:27:27.146Z
updated_at: 2026-08-09T03:05:47.620Z
closed_at: 2026-08-09T03:05:47.620Z
close_reason: "Implemented and verified in combined draft PR #15: code-step CPU/RSS sampling, step lifecycle resource events, and registered ResourcesDocument/0.1 with strict V1/V2 compatibility. Local make verify and the complete GitHub CI matrix pass."
---
resources.json uses historical metaproc.resources/v1 and /v2 tokens that the schema-token parser cannot resolve. Adopt metaproc:ResourcesDocument/0.1 for new writes, retain strict reads for historical tokens, register the standalone JSON artifact contract, and update contract documentation/tests.

## Notes

Implemented metaproc:ResourcesDocument/0.1 for new writes, strict historical V1/V2 readers, full V2 behavior compatibility, standalone SoftSchema registration, and schema-token resolver registration without a frontmatter envelope. Focused and full verification are green.
