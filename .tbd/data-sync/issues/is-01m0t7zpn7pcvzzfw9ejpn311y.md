---
type: is
id: is-01m0t7zpn7pcvzzfw9ejpn311y
title: "PR #33 review R4: make shared leaf-ceiling proof falsifiable"
kind: bug
status: closed
priority: 1
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3edn704qec0gz9asyve
created_at: 2026-08-24T15:59:49.414Z
updated_at: 2026-08-24T19:03:45.181Z
closed_at: 2026-08-24T19:03:45.181Z
close_reason: "Fixed in current PR #33 review-fix commit d5422ad. The command concurrency change is documented; executor sizing is independent; close is nonblocking; leaf and scalar ceiling proofs are falsifiable; dead profile plumbing is gone; invalid concurrency is CLIError; and the fragile barrier test was replaced. Full stack verification passed 4,346 tests with 8 skips and GitHub CI run 32765621039 is green."
resolution: null
duplicate_of: null
---
Review https://github.com/jlevy/metaproc/pull/33#issuecomment-5397584816. test_recursive_siblings_share_one_executable_leaf_ceiling cannot distinguish the leaf semaphore from the executor because both are sized by max_concurrency. Decouple executor sizing, prove the semaphore independently across sibling composites, and cover scalar-agent leaf admission.
