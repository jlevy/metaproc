---
type: is
id: is-01m0zpwpcsgetyj9bbhadb3tsh
title: "PR49 guard review: document explicit ephemeral gcp-run opt-out"
kind: bug
status: closed
priority: 2
version: 2
labels:
  - review
  - documentation
dependencies: []
parent_id: is-01m0zp9smj0e1k0ng20wc27mj2
created_at: 2026-08-26T18:56:31.641Z
updated_at: 2026-08-26T18:57:04.777Z
closed_at: 2026-08-26T18:57:04.777Z
close_reason: Added a generic Unreleased changelog entry for the explicit Filestore versus ephemeral storage posture; repository Flowmark formatting and diff check pass.
resolution: null
duplicate_of: null
---
The Filestore fail-closed guard intentionally changes user-facing CLI behavior: implicit ephemeral gcp run now fails unless --no-filestore is supplied. Add a concise Unreleased changelog entry alongside the already updated public runbook. Keep the language generic and public-safe.
