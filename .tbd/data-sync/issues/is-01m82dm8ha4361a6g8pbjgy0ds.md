---
type: is
id: is-01m82dm8ha4361a6g8pbjgy0ds
title: Protect RunPool cleanup if a backend lifecycle error races with cancellation
kind: bug
status: open
priority: 2
version: 1
spec_path: docs/project/specs/active/plan-2026-09-01-safeproc-local-incubation.md
labels: []
dependencies: []
parent_id: is-01m1fxnwnyqvq1gg8ak7317kyc
created_at: 2026-11-22T20:02:14.177Z
updated_at: 2026-11-22T20:02:14.177Z
---
Found during release review. When the backend lifecycle-error path awaits kill, a first cancellation can interrupt kill and trigger ownership release. Preserve the original backend failure, process identity fencing, and the hard shutdown deadline while making that cleanup reliable.
