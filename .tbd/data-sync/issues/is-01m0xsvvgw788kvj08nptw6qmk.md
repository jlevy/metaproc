---
type: is
id: is-01m0xsvvgw788kvj08nptw6qmk
title: "PR #48: preserve scalar teardown failure logging"
kind: bug
status: closed
priority: 0
version: 4
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels: []
dependencies:
  - type: blocks
    target: is-01m0xrg6jeywxa1hwns3eay01m
parent_id: is-01m0xrg4vr6n4znzxz0kkxxxt7
created_at: 2026-08-26T01:10:00.984Z
updated_at: 2026-08-26T01:10:48.222Z
closed_at: 2026-08-26T01:10:48.221Z
close_reason: "Rebutted after exact-source inspection: the terminal excerpt duplicated overlapping output, while the source passes exactly one formatting argument and existing teardown-failure coverage exercises the path."
resolution: canceled
duplicate_of: null
---
Fix the scalar credential-teardown exception path so logging receives the correct argument count and cannot emit a secondary logging error while preserving the original exception. Add focused regression coverage for teardown failure.
