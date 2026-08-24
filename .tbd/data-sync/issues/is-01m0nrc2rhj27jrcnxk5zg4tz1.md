---
type: is
id: is-01m0nrc2rhj27jrcnxk5zg4tz1
title: "PR #26 review L6: dead clause in the coercion guard"
kind: bug
status: closed
priority: 3
version: 2
labels: []
dependencies: []
parent_id: is-01m0nrb7r4e9ejhv5yp3q4amm0
created_at: 2026-08-22T22:10:00.081Z
updated_at: 2026-08-22T22:32:56.857Z
closed_at: 2026-08-22T22:32:56.857Z
close_reason: null
---
schema_conform.py:193 'or isinstance(value, str)' is unreachable: no member of _COERCIBLE is a str.
