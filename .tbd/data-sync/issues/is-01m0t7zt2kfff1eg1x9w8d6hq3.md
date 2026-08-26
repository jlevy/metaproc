---
type: is
id: is-01m0t7zt2kfff1eg1x9w8d6hq3
title: "PR #33 review C2: exercise force through a real composite"
kind: bug
status: closed
priority: 2
version: 5
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0r93je6fk789d26aef6wx11
created_at: 2026-08-24T15:59:52.914Z
updated_at: 2026-08-25T19:28:50.032Z
closed_at: 2026-08-25T19:28:50.031Z
close_reason: Fixed with a real composite run/resume/force integration test.
resolution: null
duplicate_of: null
---
Review https://github.com/jlevy/metaproc/pull/33#issuecomment-5397584816. Force propagation is only unit-mocked. Add an integration-level composite execution/resume test proving --force reaches real descendants without altering root-scoped skip semantics.

## Notes

Fixed: a real parent/child process now proves ordinary resume skips completed child work and --force reexecutes the child evaluator.
