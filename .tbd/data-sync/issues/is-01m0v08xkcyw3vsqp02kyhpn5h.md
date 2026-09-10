---
type: is
id: is-01m0v08xkcyw3vsqp02kyhpn5h
title: "Design: one attempt-lifecycle scope owning acquire/release order"
kind: task
status: open
priority: 2
version: 6
spec_path: docs/project/specs/active/plan-2026-09-10-runpool-execution-followups.md
labels: []
dependencies: []
parent_id: is-01m260cd2wnmjm68zd3yqaw799
hold: paused
created_at: 2026-08-24T23:04:17.259Z
updated_at: 2026-09-10T18:45:39.584Z
---
Structural remedy for the recurring leak class: permit → host admission → credential lease → state write → launch → terminal write → release is hand-woven with bespoke try/except at the scalar, pool, and mapped paths, and every leak across two review rounds was an ordering or exception-path mistake in one of the copies. Build one AsyncExitStack-shaped attempt scope that guarantees release + terminal state on every exit path including BaseException, and make it the only way executors launch work (the evidenced half of rev3 P8 governance). Holistic section 4b.

## Notes

Architecture follow-up, deliberately excluded from the consolidation. Current paths have injected BaseException and ownership regressions; introduce a generalized attempt scope only if further lifecycle work repeats this failure class after smoke.
