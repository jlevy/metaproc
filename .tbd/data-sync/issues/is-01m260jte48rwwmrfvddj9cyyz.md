---
type: is
id: is-01m260jte48rwwmrfvddj9cyyz
title: "PR 75 F3: correct admission and pressure operating guidance"
kind: bug
status: closed
priority: 1
version: 3
labels: []
dependencies: []
parent_id: is-01m260cd2wnmjm68zd3yqaw799
created_at: 2026-09-10T15:57:02.275Z
updated_at: 2026-09-10T16:23:20.359Z
closed_at: 2026-09-10T16:23:20.359Z
close_reason: Review and bounded fixes complete; full design review published on PR 75 with per-finding dispositions. Six lifecycle regressions reproduced failures before repair; make verify passed (4622 passed, 8 skipped), including audits and distribution smoke. Commit/push/CI remain tracked by mp-5v99.
resolution: null
duplicate_of: null
---
Reconcile operator and RunPool architecture claims with production: pooled host admission raises on timeout; only direct scalar admitted_launch fails open. Remove misleading global-host/safety assurances and stale control defaults or lifecycle descriptions proven by the docs audit.

## Notes

Aligned pool architecture, operator reference, artifact catalog, scalar-admission module text, fan-in fields, current retry paths, fail_run limits, and zero-work status caveat. Full review published on PR; formatter and generated-skill refresh in progress.
