---
type: is
id: is-01m0y93bza0sx42s3pr2sa94jm
title: Resolve current cryptography security advisory
kind: bug
status: closed
priority: 0
version: 2
labels:
  - supply-chain
  - security
dependencies: []
created_at: 2026-08-26T05:36:15.849Z
updated_at: 2026-08-26T06:50:10.752Z
closed_at: 2026-08-26T06:50:10.751Z
close_reason: "Rebutted after standalone reproduction: Metaproc main and PR #49 lock cryptography 50.0.0 and uv audit passes. The 49.0.0 advisory came from an enclosing consumer workspace, not Metaproc."
resolution: null
duplicate_of: null
---
The current main lock selects cryptography 49.0.0. uv audit reports GHSA-g6cj-pr64-35w5 / PYSEC-2026-3552, fixed in 50.0.0, so make verify fails at the audit gate. Assess applicability, update through the repository lock workflow or add a narrowly justified temporary waiver under SUPPLY-CHAIN-SECURITY.md, and restore a clean audit. Keep this remediation separate from unrelated feature changes.
