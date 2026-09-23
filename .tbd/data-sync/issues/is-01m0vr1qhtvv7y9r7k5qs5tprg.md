---
type: is
id: is-01m0vr1qhtvv7y9r7k5qs5tprg
title: "PR #35 R2 F6: avoid default-executor contention for filter joins"
kind: bug
status: open
priority: 3
version: 6
spec_path: docs/project/specs/active/plan-2026-09-10-runpool-execution-followups.md
labels: []
dependencies: []
parent_id: is-01m260cd2wnmjm68zd3yqaw799
hold: paused
created_at: 2026-08-25T05:59:47.513Z
updated_at: 2026-09-10T18:45:41.044Z
---
Filter-thread joins moved off the event loop via asyncio.to_thread but now share the default executor with unrelated framework work. Decide whether the existing run-owned executor is the simpler authority; fix or explicitly defer based on measured contention.

## Notes

Explicitly deferred. Keep filter joins on the standard executor until telemetry shows launch/completion delay attributable to contention; do not add another executor lifecycle speculatively.
