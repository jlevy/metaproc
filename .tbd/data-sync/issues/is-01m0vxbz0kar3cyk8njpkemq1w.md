---
type: is
id: is-01m0vxbz0kar3cyk8njpkemq1w
title: "PR #37 B7: revalidate child outputs on mapped resume"
kind: bug
status: closed
priority: 2
version: 5
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-25T07:32:45.715Z
updated_at: 2026-08-25T19:28:50.376Z
closed_at: 2026-08-25T19:28:50.376Z
close_reason: Fixed with child-output revalidation and affected-scope-only repair on mapped resume.
resolution: null
duplicate_of: null
---
Resume discovery validates only the mapped parent outputs, not declared child-process outputs. Give this a fixed or explicitly deferred disposition with a repair-path test before the relevant scale rung. Source: PR #37 senior review B7.

## Notes

Fixed: mapped resume revalidates every declared child-process output, including outputs not published by the parent, and reruns only the affected scope.
