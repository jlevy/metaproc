---
type: is
id: is-01m2kj42bvat3cn5pc16b0tbht
title: "PR #83 review P1-R1: operations summary presents a lower-bound list cost as complete"
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
parent_id: is-01m2kj39h261sajhsakeg5hhh1
created_at: 2026-09-15T22:13:40.858Z
updated_at: 2026-09-15T22:13:40.858Z
---
PR #83 part 1 R1 (Medium). _resource_figures copies totals.list_cost_usd without unpriced_models; the body renders USD 1.00 and the rollup list_cost_usd / list_cost_per_item_usd columns carry it unlabelled. engine/operations_summary.py:1243-1277, engine/operations_render.py:385, engine/operations_rollup.py:117-127, models/operations_summary.py:331.
