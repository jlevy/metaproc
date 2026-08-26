---
type: is
id: is-01m0xsdedr8apz5wvnr0r9we70
title: "PR #44 suggestion S1: isolate legacy bootstrap guard env mutation"
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
parent_id: is-01m0xrncxsdm7q87ywsha5n5x1
created_at: 2026-08-26T01:02:08.823Z
updated_at: 2026-08-26T01:02:08.823Z
---
Deferred non-blocking review suggestion from PR #44. tests/test_worker_entrypoint.py mutates METAPROC_AUTH_POOL_RUN after monkeypatch.delenv, so reordered or sharded tests can leak the variable. Reproduce and isolate the environment mutation in a separate follow-up.
