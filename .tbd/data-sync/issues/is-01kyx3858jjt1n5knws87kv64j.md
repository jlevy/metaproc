---
type: is
id: is-01kyx3858jjt1n5knws87kv64j
title: Require --contract on 'metaproc softschema compile'
kind: task
status: closed
priority: 1
version: 3
labels:
  - softschema
dependencies:
  - type: blocks
    target: is-01kyx38gn4gwmp93rst4psbm0x
parent_id: is-01kyx37mj1agq5zha1x5gn574f
created_at: 2026-07-31T22:03:23.281Z
updated_at: 2026-07-31T22:27:49.485Z
closed_at: 2026-07-31T22:27:49.484Z
close_reason: Added required --contract; landed in jlevy/metaproc#3
---
softschema 0.3 makes contract_id a required keyword on compile_model, so the sidecar always records its contract. Add a required --contract option to the compile command and pass it through. Breaking CLI change: document in CHANGELOG per AGENTS.md 'preserve public CLI flags unless the change includes a migration plan'.
