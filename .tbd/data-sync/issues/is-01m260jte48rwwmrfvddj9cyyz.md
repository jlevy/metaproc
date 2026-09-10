---
type: is
id: is-01m260jte48rwwmrfvddj9cyyz
title: "PR 75 F3: correct admission and pressure operating guidance"
kind: bug
status: closed
priority: 1
version: 6
spec_path: docs/project/specs/active/plan-2026-09-10-runpool-execution-followups.md
labels: []
dependencies: []
parent_id: is-01m260cd2wnmjm68zd3yqaw799
created_at: 2026-09-10T15:57:02.275Z
updated_at: 2026-09-10T18:48:55.410Z
closed_at: 2026-09-10T16:23:20.359Z
close_reason: Review and bounded fixes complete; full design review published on PR 75 with per-finding dispositions. Six lifecycle regressions reproduced failures before repair; make verify passed (4622 passed, 8 skipped), including audits and distribution smoke. Commit/push/CI remain tracked by mp-5v99.
resolution: null
duplicate_of: null
---
Reconcile operator and RunPool architecture claims with production: standalone pool-owned host admission raises on timeout, while the outer scalar execution gate can fail open and also wraps mapped leaves submitted to the run-owned pool. Correct misleading global-host guarantees, disk-pressure control claims, blanket cap advice, and stale lifecycle descriptions. Implemented in PR75 with regenerated help/skill documentation and verified alongside F1-F2.

## Notes

Aligned pool architecture, operator reference, artifact catalog, scalar-admission module text, fan-in fields, current retry paths, fail_run limits, and zero-work status caveat. Full review published on PR; formatter and generated-skill refresh in progress.
