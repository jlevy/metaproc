---
type: is
id: is-01m0t7zt2kfff1eg1x9w8d6hq3
title: "PR #33 review C2: exercise force through a real composite"
kind: bug
status: open
priority: 2
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3edn704qec0gz9asyve
created_at: 2026-08-24T15:59:52.914Z
updated_at: 2026-08-24T15:59:52.914Z
---
Review https://github.com/jlevy/metaproc/pull/33#issuecomment-5397584816. Force propagation is only unit-mocked. Add an integration-level composite execution/resume test proving --force reaches real descendants without altering root-scoped skip semantics.
