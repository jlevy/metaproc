---
type: is
id: is-01m0nrc16zz3rhwyn0h6pv2av8
title: "PR #26 review M4: conform_declared_outputs is duck-typed where its sibling is not"
kind: bug
status: closed
priority: 1
version: 2
labels: []
dependencies: []
parent_id: is-01m0nrb7r4e9ejhv5yp3q4amm0
created_at: 2026-08-22T22:09:58.495Z
updated_at: 2026-08-22T22:32:56.847Z
closed_at: 2026-08-22T22:32:56.847Z
close_reason: null
---
src/metaproc/engine/schema_conform.py:294-335 takes Mapping[str, Any] / registry: Any and uses six getattr calls. repair_declared_outputs takes dict[str, IOSpec]. Renaming IOSpec.format silently disables the pass with every test still green.
