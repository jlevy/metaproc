---
type: is
id: is-01m0t7zqm3kx2kkj4m1hpnfvk4
title: "PR #33 review R6: avoid holding leaf capacity during waits"
kind: bug
status: open
priority: 2
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3edn704qec0gz9asyve
created_at: 2026-08-24T15:59:50.403Z
updated_at: 2026-08-24T15:59:50.403Z
---
Review https://github.com/jlevy/metaproc/pull/33#issuecomment-5397584816. Leaves hold admission across host gates or retry sleeps, and fan-out queue order can starve sibling scopes. Place non-work waits outside executable capacity and add mapped-sibling fairness evidence.
