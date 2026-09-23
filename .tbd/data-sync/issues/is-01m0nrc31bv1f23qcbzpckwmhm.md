---
type: is
id: is-01m0nrc31bv1f23qcbzpckwmhm
title: "PR #26 review S1: model_json_schema rebuilt per item in the fan-out loop"
kind: bug
status: closed
priority: 3
version: 2
labels: []
dependencies: []
parent_id: is-01m0nrb7r4e9ejhv5yp3q4amm0
created_at: 2026-08-22T22:10:00.363Z
updated_at: 2026-08-22T22:32:57.337Z
closed_at: 2026-08-22T22:32:57.337Z
close_reason: "Dissolved by the same rewrite: model_json_schema() is no longer called. The replacement is model_validate, which is per-document and necessary."
---
schema_conform.py:339. 127 items x N outputs per run; pydantic does not cache across calls.
