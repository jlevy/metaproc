---
type: is
id: is-01m0x3tct1c5x26xwv0dw4g149
title: "R4: do not expose carried terminal status while an orchestrator is active"
kind: bug
status: closed
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0x358va0njc6k4g00pccj7e
created_at: 2026-08-25T18:44:44.480Z
updated_at: 2026-08-25T19:25:25.686Z
closed_at: 2026-08-25T19:25:25.686Z
close_reason: Fixed with live-ownership precedence, fresh running projection, and status regressions.
resolution: null
duplicate_of: null
---
A resume can acquire the orchestrator lease before rewriting process-status.yaml. During that interval status, wait, and completion checks currently let the prior failed or cancelled state outrank live ownership and can return a false terminal verdict. Make active ownership outrank carried terminal state, write a fresh running projection at orchestration entry, and add focused regressions.

## Notes

Fixed: active orchestrator ownership outranks carried terminal status and recursive orchestration writes a fresh running projection. Status/wait regressions pass.
