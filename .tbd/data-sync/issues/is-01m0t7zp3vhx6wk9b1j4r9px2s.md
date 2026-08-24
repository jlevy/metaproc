---
type: is
id: is-01m0t7zp3vhx6wk9b1j4r9px2s
title: "PR #33 review R3: make execution-context close nonblocking"
kind: bug
status: closed
priority: 1
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3edn704qec0gz9asyve
created_at: 2026-08-24T15:59:48.858Z
updated_at: 2026-08-24T19:03:45.173Z
closed_at: 2026-08-24T19:03:45.173Z
close_reason: "Fixed in current PR #33 review-fix commit d5422ad. The command concurrency change is documented; executor sizing is independent; close is nonblocking; leaf and scalar ceiling proofs are falsifiable; dead profile plumbing is gone; invalid concurrency is CLIError; and the fragile barrier test was replaced. Full stack verification passed 4,346 tests with 8 skips and GitHub CI run 32765621039 is green."
resolution: null
duplicate_of: null
---
Review https://github.com/jlevy/metaproc/pull/33#issuecomment-5397584816. RunExecutionContext.close calls ThreadPoolExecutor.shutdown(wait=True) from the coroutine finally path and can block the event loop for an unbounded running subprocess. Use a nonblocking or bounded off-loop shutdown while preserving lifecycle ownership.
