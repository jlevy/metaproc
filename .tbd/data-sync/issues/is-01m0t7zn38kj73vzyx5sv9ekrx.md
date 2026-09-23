---
type: is
id: is-01m0t7zn38kj73vzyx5sv9ekrx
title: "PR #33 review R1: disclose command-step concurrency change"
kind: bug
status: closed
priority: 1
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3edn704qec0gz9asyve
created_at: 2026-08-24T15:59:47.815Z
updated_at: 2026-08-24T19:03:45.161Z
closed_at: 2026-08-24T19:03:45.160Z
close_reason: "Fixed in current PR #33 review-fix commit d5422ad. The command concurrency change is documented; executor sizing is independent; close is nonblocking; leaf and scalar ceiling proofs are falsifiable; dead profile plumbing is gone; invalid concurrency is CLIError; and the fragile barrier test was replaced. Full stack verification passed 4,346 tests with 8 skips and GitHub CI run 32765621039 is green."
resolution: null
duplicate_of: null
---
Review https://github.com/jlevy/metaproc/pull/33#issuecomment-5397584816. run_process.py command-backed code steps moved from accidental event-loop serialization to run-owned executor concurrency, without write-boundary protection. Document the concurrency and shared-process_dir risk in CHANGELOG and operator reference.
