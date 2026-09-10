---
type: is
id: is-01m260z0663rrdsqt0drpvdq5f
title: "PR 75 F4: preserve failed-step causes in run summaries"
kind: bug
status: open
priority: 1
version: 1
labels: []
dependencies: []
parent_id: is-01m260cd2wnmjm68zd3yqaw799
created_at: 2026-09-10T16:03:41.381Z
updated_at: 2026-09-10T16:03:41.381Z
---
Review at 851989a: commands/run_process.py:1200-1217 returns no recovered error for mapped, agent, or manual steps; item_runner StepInvoker bool / run_fan_out tuple cannot carry causes. Define a typed step outcome with bounded FailureClass/OutputFailureKind counts and artifact references, reusing fan_in collectors. Verify scalar/mapped/manual/composite failure summaries and process-events. Full review on PR #75; larger persisted-contract change deferred for review.
