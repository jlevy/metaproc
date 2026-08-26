---
type: is
id: is-01m0vnhtk2w34td9qsgyf7vmbr
title: "PR #33 N5: reject invalid run ceilings before run setup"
kind: bug
status: closed
priority: 2
version: 4
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-25T05:16:09.185Z
updated_at: 2026-08-25T19:28:49.629Z
closed_at: 2026-08-25T19:28:49.628Z
close_reason: Fixed with pre-resolution validation for explicit and environment-derived invalid ceilings.
resolution: null
duplicate_of: null
---
PR #33 round-2 review fresh note N5: explicit or environment-derived max_concurrency below 1 was rejected only when RunExecutionContext.create ran after process resolution, run-directory setup, and lease acquisition. Validate immediately after CLI environment fallback so invalid capacity cannot mutate run state. Review: https://github.com/jlevy/metaproc/pull/33#issuecomment-5402358325

## Notes

Fixed: CLI and environment-derived ceilings below one fail before process resolution, run setup, or lease acquisition; regression and full verification pass.
