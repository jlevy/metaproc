---
type: is
id: is-01m0t7zthnzm9zcqnt2bffh0rd
title: "PR #33 review C3: replace fragile 33-thread barrier test"
kind: bug
status: closed
priority: 3
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3edn704qec0gz9asyve
created_at: 2026-08-24T15:59:53.396Z
updated_at: 2026-08-24T19:03:45.207Z
closed_at: 2026-08-24T19:03:45.207Z
close_reason: "Fixed in current PR #33 review-fix commit d5422ad. The command concurrency change is documented; executor sizing is independent; close is nonblocking; leaf and scalar ceiling proofs are falsifiable; dead profile plumbing is gone; invalid concurrency is CLIError; and the fragile barrier test was replaced. Full stack verification passed 4,346 tests with 8 skips and GitHub CI run 32765621039 is green."
resolution: null
duplicate_of: null
---
Review https://github.com/jlevy/metaproc/pull/33#issuecomment-5397584816. test_command_steps_honor_executor_ceiling_above_asyncio_default depends on a 5-second Barrier across 33 lazily spawned threads, so scheduler delay yields misleading failures. Replace with deterministic coordination.
