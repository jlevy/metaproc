---
type: is
id: is-01m0tc0zayh1avcbtqndky17em
title: Remove the stale Prefect-flow test ignore
kind: bug
status: closed
priority: 2
version: 3
labels: []
dependencies: []
parent_id: is-01m0tc0msnehs7xyvhpbekkw45
created_at: 2026-08-24T17:10:25.373Z
updated_at: 2026-08-24T18:02:06.518Z
closed_at: 2026-08-24T18:02:06.518Z
close_reason: Removed the obsolete command, split-state, gateway, and hybrid surfaces; preserved local execution and full-cloud Batch paths. Focused cloud/CLI tests, explicit local/resume regressions, full 4,259-test suite, lint/type/link/public-hygiene/supply-chain/browser/Markdown checks, vulnerability audits, and distribution smoke all pass.
resolution: null
duplicate_of: null
---
Delete conftest.py's ignore for the nonexistent cloud/gcp/prefect_flow.py and reconcile the standalone preview plan's open cleanup item as removed.
