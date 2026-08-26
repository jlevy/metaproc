---
type: is
id: is-01m0vxbyq2q0vffmgwbb8q8j60
title: "PR #37 B5: cancel mapped parent attempts terminally"
kind: bug
status: closed
priority: 1
version: 5
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies:
  - type: blocks
    target: is-01m0vhs620ptcvxv074ccx88z4
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-25T07:32:45.407Z
updated_at: 2026-08-25T19:28:48.980Z
closed_at: 2026-08-25T19:28:48.979Z
close_reason: Fixed with terminal mapped-parent cancellation state and regression coverage.
resolution: null
duplicate_of: null
---
A CancelledError from a mapped child currently re-raises without ending the mapped parent attempt, leaving status running until later reconciliation. Mark the attempt with cancelled disposition before propagation and add a cancellation regression. Source: PR #37 senior review B5.

## Notes

Fixed: mapped cancellation terminalizes the parent item as cancelled before propagation; focused cancellation and full verification pass.
