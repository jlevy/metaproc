---
type: is
id: is-01m0t7zrjkknb158w75jva83n7
title: "PR #33 review R8: surface invalid max concurrency as CLIError"
kind: bug
status: closed
priority: 2
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3edn704qec0gz9asyve
created_at: 2026-08-24T15:59:51.379Z
updated_at: 2026-08-24T19:03:45.198Z
closed_at: 2026-08-24T19:03:45.198Z
close_reason: "Fixed in current PR #33 review-fix commit d5422ad. The command concurrency change is documented; executor sizing is independent; close is nonblocking; leaf and scalar ceiling proofs are falsifiable; dead profile plumbing is gone; invalid concurrency is CLIError; and the fragile barrier test was replaced. Full stack verification passed 4,346 tests with 8 skips and GitHub CI run 32765621039 is green."
resolution: null
duplicate_of: null
---
Review https://github.com/jlevy/metaproc/pull/33#issuecomment-5397584816. RunExecutionContext.create raises bare ValueError for max_concurrency < 1, so an invalid default environment value produces a traceback. Validate at the CLI boundary and raise CLIError.
