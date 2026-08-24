---
type: is
id: is-01m0t7zr32yz748ccnexyfp2k3
title: "PR #33 review R7: remove dead fan-out profile_files plumbing"
kind: bug
status: open
priority: 2
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3edn704qec0gz9asyve
created_at: 2026-08-24T15:59:50.881Z
updated_at: 2026-08-24T15:59:50.881Z
---
Review https://github.com/jlevy/metaproc/pull/33#issuecomment-5397584816. _execute_fan_out_step accepts profile_files but does not consume it, and this PR adds another caller. Delete the parameter or make the contract real rather than carrying dead policy plumbing.
