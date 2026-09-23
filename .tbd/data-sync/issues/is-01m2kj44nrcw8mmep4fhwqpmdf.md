---
type: is
id: is-01m2kj44nrcw8mmep4fhwqpmdf
title: "PR #83 review P1-R8: agents table omits cache-write tokens; pressure checks counted as health samples"
kind: bug
status: closed
priority: 3
version: 2
labels: []
dependencies: []
parent_id: is-01m2kj39h261sajhsakeg5hhh1
created_at: 2026-09-15T22:13:43.223Z
updated_at: 2026-09-16T00:17:06.137Z
closed_at: 2026-09-16T00:17:06.135Z
close_reason: "Fixed in 381f378: the Agents table shows cache write tokens; pressure checks are counted and named by sample_source. Tests: test_agents_table_shows_every_token_bucket, test_pressure_checks_stand_in_for_absent_health_samples_under_their_own_name."
resolution: null
duplicate_of: null
---
PR #83 part 1 R8 (Low). engine/operations_render.py:345-348, engine/operations_summary.py:935-970.
