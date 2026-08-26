---
type: is
id: is-01m0zs3v3h8dx4xw63wbywn0m1
title: "PR #49 review M8: stabilize and strictly version the public projection DTO"
kind: bug
status: open
priority: 2
version: 1
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - review
dependencies: []
parent_id: is-01m0zs1svbsptksz66728wzdrb
created_at: 2026-08-26T19:35:22.993Z
updated_at: 2026-08-26T19:35:22.993Z
---
The VizModel projection currently embeds oversized runtime models behind a free-form schema token and permissive extra handling. Prefer a narrow versioned DTO; at minimum enforce Literal schema tokens, forbid unknown fields, and prove VizModel/0.3 compatibility.
