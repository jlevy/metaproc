---
type: is
id: is-01m0tjefebxck534azhjgd5ew9
title: "PR #36 review D1: audit the hard-coded fan-out cooling policy"
kind: bug
status: open
priority: 2
version: 3
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels: []
dependencies: []
parent_id: is-01m0s0r624c0eszrgnq4qgjjbe
hold: paused
created_at: 2026-08-24T19:02:39.306Z
updated_at: 2026-08-24T19:04:22.170Z
---
Review https://github.com/jlevy/metaproc/pull/36#issuecomment-5397585537. Fan-out currently treats pool exhaustion with a message-matched 60-second-to-30-minute cooling requeue that is not bounded by max_retries or a run-level policy, while scalar execution fails fast. Before retaining or replacing it, verify released consumers and v3.0-pre smoke evidence; then remove it or make one existing policy govern both paths without a second scheduler.

## Notes

Deferred from PR #36 review D1. Characterize the existing message-matched fan-out cooling loop during the audit; remove or minimally unify it only if released-consumer or v3.0-pre evidence requires the behavior.
