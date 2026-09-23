---
type: is
id: is-01m0tc0ygm8r3zh2z98atpkzb8
title: Delete the unregistered gcp archive command
kind: bug
status: closed
priority: 1
version: 3
labels: []
dependencies: []
parent_id: is-01m0tc0msnehs7xyvhpbekkw45
created_at: 2026-08-24T17:10:24.531Z
updated_at: 2026-08-24T18:02:06.489Z
closed_at: 2026-08-24T18:02:06.488Z
close_reason: Removed the obsolete command, split-state, gateway, and hybrid surfaces; preserved local execution and full-cloud Batch paths. Focused cloud/CLI tests, explicit local/resume regressions, full 4,259-test suite, lint/type/link/public-hygiene/supply-chain/browser/Markdown checks, vulnerability audits, and distribution smoke all pass.
resolution: null
duplicate_of: null
---
Remove metaproc gcp archive and its gsutil rsync/--delete-local behavior, tests, and docs. It writes unregistered archives without typed ownership, claims, receipts, byte verification, or cluster-safe writer checks. Durable publication belongs to the consumer's canonical run-data contract.
