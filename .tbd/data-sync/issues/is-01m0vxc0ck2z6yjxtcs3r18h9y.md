---
type: is
id: is-01m0vxc0ck2z6yjxtcs3r18h9y
title: "PR #37 B12: expose mapped items in events and projections"
kind: feature
status: closed
priority: 2
version: 6
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-25T07:32:47.123Z
updated_at: 2026-08-25T19:28:51.880Z
closed_at: 2026-08-25T19:28:51.879Z
close_reason: Fixed for the minimum slice with mapped item events and recursive contained operator views; richer presentation remains under mp-1af0.
resolution: null
duplicate_of: null
---
Mapped items lack per-item process events and are not visible in current visualization and resource projections. Define the minimum generic observability needed to inspect mapped-scope execution, and defer richer projection only with an explicit disposition. Source: pull request 37 senior review B12.

## Notes

Fixed for the minimum slice: mapped items emit start/complete/fail events; nested process, pool, status, and trace views discover contained child scopes. Richer artifact presentation remains under mp-1af0.
