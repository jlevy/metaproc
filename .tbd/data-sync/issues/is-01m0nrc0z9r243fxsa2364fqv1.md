---
type: is
id: is-01m0nrc0z9r243fxsa2364fqv1
title: "PR #26 review M3: promote _resolve_output_fpath to a public shared helper"
kind: bug
status: closed
priority: 1
version: 2
labels: []
dependencies: []
parent_id: is-01m0nrb7r4e9ejhv5yp3q4amm0
created_at: 2026-08-22T22:09:58.248Z
updated_at: 2026-08-22T22:32:56.846Z
closed_at: 2026-08-22T22:32:56.846Z
close_reason: null
---
src/metaproc/engine/schema_conform.py:44 imports the private _resolve_output_fpath from engine.validation. Four call sites now depend on identical resolution. Promote to resolve_output_fpath and pin with TestOutputResolution. Same as the author's mp-wxnp on feat/semantic-kernel-rfc (bbb1fa2).
