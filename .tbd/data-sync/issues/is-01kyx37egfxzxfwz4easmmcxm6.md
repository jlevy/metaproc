---
type: is
id: is-01kyx37egfxzxfwz4easmmcxm6
title: Upgrade to softschema 0.3 and release
kind: epic
status: closed
priority: 1
version: 2
labels: []
dependencies: []
created_at: 2026-07-31T22:02:59.982Z
updated_at: 2026-07-31T22:03:51.315Z
closed_at: 2026-07-31T22:03:51.314Z
close_reason: Duplicate of mp-ivpj (created twice by a scripting error)
---
softschema 0.3 tightens the contract-ID grammar and makes compile_model's contract_id required. Metaproc must adopt it, then cut a release so downstream consumers (trading) can pin a published version instead of a git branch.
