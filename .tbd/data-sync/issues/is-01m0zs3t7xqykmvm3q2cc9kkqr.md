---
type: is
id: is-01m0zs3t7xqykmvm3q2cc9kkqr
title: "PR #49 review H5: keep projection errors from becoming MetaBrowser 500s"
kind: bug
status: closed
priority: 1
version: 4
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - review
dependencies: []
parent_id: is-01m0zs1svbsptksz66728wzdrb
created_at: 2026-08-26T19:35:22.108Z
updated_at: 2026-08-26T20:01:31.187Z
closed_at: 2026-08-26T20:01:31.187Z
close_reason: "Fixed and validated in e1b9de2; per-finding disposition published on PR #49 and all five CI jobs passed."
resolution: null
duplicate_of: null
---
Expected projection/read/identity errors currently escape viz_model_handler. Preserve structural visualization and return a typed validation warning or deliberate 4xx instead of an accidental HTTP 500.

## Notes

Fixed in the shared PR #49 working tree, pending parent integration commit: viz-model now catches progress and task-projection OSError/ValueError/CLI failures independently, returns stable code/message validation warnings, and still serializes the structural process graph. Typed warnings render correctly in the Visual tab. Focused browser/sidekick suite: 38 passed; Ruff, BasedPyright, Biome, TypeScript, and diff checks clean.
