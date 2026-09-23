---
type: is
id: is-01m0tc0z29tc6qjfskjp6pb4qq
title: Fail closed for unsupported laptop-orchestrator hybrid runs
kind: bug
status: closed
priority: 1
version: 3
labels: []
dependencies: []
parent_id: is-01m0tc0msnehs7xyvhpbekkw45
created_at: 2026-08-24T17:10:25.097Z
updated_at: 2026-08-24T18:02:06.510Z
closed_at: 2026-08-24T18:02:06.510Z
close_reason: Removed the obsolete command, split-state, gateway, and hybrid surfaces; preserved local execution and full-cloud Batch paths. Focused cloud/CLI tests, explicit local/resume regressions, full 4,259-test suite, lint/type/link/public-hygiene/supply-chain/browser/Markdown checks, vulnerability audits, and distribution smoke all pass.
resolution: null
duplicate_of: null
---
run-process currently warns when a laptop run tree cannot be seen by gcp-worker Batch tasks, while workers always resolve RUNS_DIR on Filestore. Reject this topology unless a real declared shared transport exists; do not maintain path aliases or best-effort compatibility.
