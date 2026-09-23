---
type: is
id: is-01m0nrc1pkya8fsxp8myepkvvc
title: "PR #26 review L2: gz sibling breaks the 'same file validation reads' claim"
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m0nrb7r4e9ejhv5yp3q4amm0
created_at: 2026-08-22T22:09:58.995Z
updated_at: 2026-08-22T22:32:56.851Z
closed_at: 2026-08-22T22:32:56.851Z
close_reason: null
---
schema_conform.py:344 gates on fpath.is_file(); validation.py:369-374 resolves a .gz sibling via artifact_exists/resolve_existing_artifact. Shared with repair_declared_outputs.
