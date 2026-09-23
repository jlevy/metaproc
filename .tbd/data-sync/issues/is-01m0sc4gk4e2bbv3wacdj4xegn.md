---
type: is
id: is-01m0sc4gk4e2bbv3wacdj4xegn
title: "PR #30 review S5: Replace line-number env allowlist identities"
kind: task
status: open
priority: 3
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0sbeq4f6ac3ayz46z7kc03h
created_at: 2026-08-24T07:53:06.907Z
updated_at: 2026-08-24T07:53:06.907Z
---
Additional review note from https://github.com/jlevy/metaproc/pull/30#issuecomment-5392026692. tests/test_env_vars_coverage.py keys exemptions to source line numbers, so unrelated edits above a call site break the gate. This PR will re-pin the existing exemption; a follow-up should identify allowed call sites by a stable AST or source identity.
