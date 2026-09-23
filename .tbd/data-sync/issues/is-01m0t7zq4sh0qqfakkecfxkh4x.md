---
type: is
id: is-01m0t7zq4sh0qqfakkecfxkh4x
title: "PR #33 review R5: distinguish queued from running scalar steps"
kind: bug
status: closed
priority: 2
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3edn704qec0gz9asyve
created_at: 2026-08-24T15:59:49.912Z
updated_at: 2026-08-24T19:03:45.888Z
closed_at: 2026-08-24T19:03:45.888Z
close_reason: "Fixed in the stacked authentication rung now at PR #34 head 3d11a64: admission and credential acquisition precede durable running/attempt state. Included in the 4,346-test stack verification and green exact-head GitHub CI run 32765621039."
resolution: null
duplicate_of: null
---
Review https://github.com/jlevy/metaproc/pull/33#issuecomment-5397584816. Scalar steps write running state and attempts before acquiring leaf admission, inflating elapsed time and making queued work indistinguishable. Move writes inside admission or persist an admitted timestamp; confirm stacked PR #34 ordering.
