---
type: is
id: is-01m0t7zq4sh0qqfakkecfxkh4x
title: "PR #33 review R5: distinguish queued from running scalar steps"
kind: bug
status: open
priority: 2
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3edn704qec0gz9asyve
created_at: 2026-08-24T15:59:49.912Z
updated_at: 2026-08-24T15:59:49.912Z
---
Review https://github.com/jlevy/metaproc/pull/33#issuecomment-5397584816. Scalar steps write running state and attempts before acquiring leaf admission, inflating elapsed time and making queued work indistinguishable. Move writes inside admission or persist an admitted timestamp; confirm stacked PR #34 ordering.
