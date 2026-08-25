---
type: is
id: is-01m0x35m3t0fqztw0mnqtw2x9w
title: "R1: make composite-scope discovery recursive and contained"
kind: bug
status: closed
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0x358va0njc6k4g00pccj7e
created_at: 2026-08-25T18:33:23.833Z
updated_at: 2026-08-25T19:25:24.400Z
closed_at: 2026-08-25T19:25:24.394Z
close_reason: Fixed with recursive contained discovery and regression coverage; local exact-head verification passed.
resolution: null
duplicate_of: null
---
The operator-view walker used an arbitrary filesystem depth and followed resolved scope paths without checking containment. Valid deeper recursive scopes could disappear from pool/status/trace views, while an in-run symlink could make discovery leave the run tree. Replace it with recursion over the runtime-owned <scope>/<step>[/<item>] shape, enforce resolved-root containment, and cover deep recursion and symlink escape.

## Notes

Fixed: recursive operator discovery now follows runtime-owned nested scope shapes, enforces resolved-root containment, and has deep-recursion and symlink-escape regressions. Full local make verify passes.
