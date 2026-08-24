---
type: is
id: is-01m0t7zt2kfff1eg1x9w8d6hq3
title: "PR #33 review C2: exercise force through a real composite"
kind: bug
status: open
priority: 2
version: 2
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0r93je6fk789d26aef6wx11
created_at: 2026-08-24T15:59:52.914Z
updated_at: 2026-08-24T19:03:44.480Z
---
Review https://github.com/jlevy/metaproc/pull/33#issuecomment-5397584816. Force propagation is only unit-mocked. Add an integration-level composite execution/resume test proving --force reaches real descendants without altering root-scoped skip semantics.

## Notes

Deferred from PR #33 coverage note C2. Add a real composite force/resume test with the per-item recovery slice; the current #33 characterization deliberately proves only run-level force propagation.
