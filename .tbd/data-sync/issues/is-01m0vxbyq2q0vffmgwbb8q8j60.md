---
type: is
id: is-01m0vxbyq2q0vffmgwbb8q8j60
title: "PR #37 B5: cancel mapped parent attempts terminally"
kind: bug
status: open
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - pr-review
dependencies:
  - type: blocks
    target: is-01m0vhs620ptcvxv074ccx88z4
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-25T07:32:45.407Z
updated_at: 2026-08-25T07:33:12.658Z
---
A CancelledError from a mapped child currently re-raises without ending the mapped parent attempt, leaving status running until later reconciliation. Mark the attempt with cancelled disposition before propagation and add a cancellation regression. Source: PR #37 senior review B5.
