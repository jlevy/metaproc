---
type: is
id: is-01m0zs3sy2zbw9ee7tebw4bk4e
title: "PR #49 review H4: represent artifact availability and declared kind"
kind: bug
status: closed
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - review
dependencies: []
parent_id: is-01m0zs1svbsptksz66728wzdrb
created_at: 2026-08-26T19:35:21.794Z
updated_at: 2026-08-26T20:01:31.181Z
closed_at: 2026-08-26T20:01:31.181Z
close_reason: "Fixed and validated in e1b9de2; per-finding disposition published on PR #49 and all five CI jobs passed."
resolution: null
duplicate_of: null
---
Projected accepted paths may be missing after partial hydration/deletion and may not match the declared file/directory kind. Preserve diagnostic evidence but make consumable availability explicit and fail closed for absent or kind-incompatible artifacts.
