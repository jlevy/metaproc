---
type: is
id: is-01m0t7zr32yz748ccnexyfp2k3
title: "PR #33 review R7: remove dead fan-out profile_files plumbing"
kind: bug
status: closed
priority: 2
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3edn704qec0gz9asyve
created_at: 2026-08-24T15:59:50.881Z
updated_at: 2026-08-24T19:03:45.190Z
closed_at: 2026-08-24T19:03:45.190Z
close_reason: "Fixed in current PR #33 review-fix commit d5422ad. The command concurrency change is documented; executor sizing is independent; close is nonblocking; leaf and scalar ceiling proofs are falsifiable; dead profile plumbing is gone; invalid concurrency is CLIError; and the fragile barrier test was replaced. Full stack verification passed 4,346 tests with 8 skips and GitHub CI run 32765621039 is green."
resolution: null
duplicate_of: null
---
Review https://github.com/jlevy/metaproc/pull/33#issuecomment-5397584816. _execute_fan_out_step accepts profile_files but does not consume it, and this PR adds another caller. Delete the parameter or make the contract real rather than carrying dead policy plumbing.
