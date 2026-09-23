---
type: is
id: is-01m0nrc0fankyvnczpf3g4x104
title: "PR #26 review M1: additionalProperties and prefixItems invisible to the schema walker"
kind: bug
status: closed
priority: 1
version: 2
labels: []
dependencies: []
parent_id: is-01m0nrb7r4e9ejhv5yp3q4amm0
created_at: 2026-08-22T22:09:57.738Z
updated_at: 2026-08-22T22:32:57.095Z
closed_at: 2026-08-22T22:32:57.094Z
close_reason: "Dissolved by the pydantic-driven rewrite in 58332ac: the JSON Schema walker this described is gone, and pydantic's string_type locations reach dict[str, X], tuples and optional unions. Kept as regression tests in TestShapesTheModelDescribes."
---
src/metaproc/engine/schema_conform.py:159 _prop_schema reads only 'properties'; :174 _item_schema reads only 'items'. pydantic emits additionalProperties for dict[str,X] and prefixItems for tuple[...]. Both fall through silently — the exact bug class the pass exists to fix.
