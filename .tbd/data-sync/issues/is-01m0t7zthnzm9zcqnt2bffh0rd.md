---
type: is
id: is-01m0t7zthnzm9zcqnt2bffh0rd
title: "PR #33 review C3: replace fragile 33-thread barrier test"
kind: bug
status: open
priority: 3
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3edn704qec0gz9asyve
created_at: 2026-08-24T15:59:53.396Z
updated_at: 2026-08-24T15:59:53.396Z
---
Review https://github.com/jlevy/metaproc/pull/33#issuecomment-5397584816. test_command_steps_honor_executor_ceiling_above_asyncio_default depends on a 5-second Barrier across 33 lazily spawned threads, so scheduler delay yields misleading failures. Replace with deterministic coordination.
