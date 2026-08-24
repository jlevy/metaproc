---
type: is
id: is-01m0t7zqm3kx2kkj4m1hpnfvk4
title: "PR #33 review R6: avoid holding leaf capacity during waits"
kind: bug
status: open
priority: 2
version: 2
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0r93k0sfy1ye28jj2f7db1z
created_at: 2026-08-24T15:59:50.403Z
updated_at: 2026-08-24T19:03:42.237Z
---
Review https://github.com/jlevy/metaproc/pull/33#issuecomment-5397584816. Leaves hold admission across host gates or retry sleeps, and fan-out queue order can starve sibling scopes. Place non-work waits outside executable capacity and add mapped-sibling fairness evidence.

## Notes

Deferred from PR #33 review R6. The remaining issue is admission held across host or retry waits plus mapped-sibling fairness. Resolve with the one host byte authority and measure fairness in the smoke ladder; do not add a second scheduler.
