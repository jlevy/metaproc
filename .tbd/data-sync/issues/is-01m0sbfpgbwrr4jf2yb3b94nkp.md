---
type: is
id: is-01m0sbfpgbwrr4jf2yb3b94nkp
title: "PR #30 review R3: Skip incidental non-regular files in default workspace scan"
kind: bug
status: closed
priority: 1
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0sbeq4f6ac3ayz46z7kc03h
created_at: 2026-08-24T07:41:44.842Z
updated_at: 2026-08-24T08:11:43.566Z
closed_at: 2026-08-24T08:11:43.566Z
close_reason: "Fixed in 1cbe8e8; Metaproc PR #30 canonical five-job CI is green."
resolution: null
duplicate_of: null
---
PR #30 review R3 (Medium), src/metaproc/cloud/gcp/dispatch_artifacts.py:127-128. Warn and skip sockets or FIFOs discovered by the default git scan while keeping explicit sync-only and extra-path requests fail-closed. Review: https://github.com/jlevy/metaproc/pull/30#issuecomment-5392026692
