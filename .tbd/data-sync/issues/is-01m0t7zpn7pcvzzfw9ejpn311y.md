---
type: is
id: is-01m0t7zpn7pcvzzfw9ejpn311y
title: "PR #33 review R4: make shared leaf-ceiling proof falsifiable"
kind: bug
status: open
priority: 1
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3edn704qec0gz9asyve
created_at: 2026-08-24T15:59:49.414Z
updated_at: 2026-08-24T15:59:49.414Z
---
Review https://github.com/jlevy/metaproc/pull/33#issuecomment-5397584816. test_recursive_siblings_share_one_executable_leaf_ceiling cannot distinguish the leaf semaphore from the executor because both are sized by max_concurrency. Decouple executor sizing, prove the semaphore independently across sibling composites, and cover scalar-agent leaf admission.
