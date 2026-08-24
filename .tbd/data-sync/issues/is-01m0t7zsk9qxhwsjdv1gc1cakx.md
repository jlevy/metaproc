---
type: is
id: is-01m0t7zsk9qxhwsjdv1gc1cakx
title: "PR #33 review C1: prove one fan-out semaphore across siblings"
kind: bug
status: closed
priority: 2
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3edn704qec0gz9asyve
created_at: 2026-08-24T15:59:52.425Z
updated_at: 2026-08-24T19:03:46.629Z
closed_at: 2026-08-24T19:03:46.627Z
close_reason: Rebutted as redundant implementation-coupled coverage. The execution layer passes execution_context.leaf_semaphore by identity; composite recursion tests prove the identical RunExecutionContext crosses scopes, and RunPool behavior tests prove one external semaphore limits multiple pools. The mapped M0 additionally proves one context and one leaf ceiling across sibling scopes.
resolution: null
duplicate_of: null
---
Review https://github.com/jlevy/metaproc/pull/33#issuecomment-5397584816. Add a test that sibling composite fan-outs receive the exact same RunPoolConfig.external_semaphore object, not merely equal counts.
