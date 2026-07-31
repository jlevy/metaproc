---
type: is
id: is-01kyx37mj1agq5zha1x5gn574f
title: Upgrade to softschema 0.3 and release
kind: epic
status: open
priority: 1
version: 6
labels: []
dependencies: []
child_order_hints:
  - is-01kyx3858jjt1n5knws87kv64j
  - is-01kyx385g5c392kmb9zga9qhm6
  - is-01kyx385qhs8bt0wzr4r4d25mh
  - is-01kyx385z9ab0pdr0aajs4c32e
  - is-01kyx38gn4gwmp93rst4psbm0x
created_at: 2026-07-31T22:03:06.176Z
updated_at: 2026-07-31T22:03:34.947Z
---
softschema 0.3 tightens the contract-ID grammar and makes compile_model contract_id required. Metaproc must adopt it, then cut a release so downstream consumers can pin a published version instead of a git branch.
