---
type: is
id: is-01m260jte48rwwmrfvddj9cyyz
title: "PR 75 F3: correct admission and pressure operating guidance"
kind: bug
status: open
priority: 1
version: 1
labels: []
dependencies: []
parent_id: is-01m260cd2wnmjm68zd3yqaw799
created_at: 2026-09-10T15:57:02.275Z
updated_at: 2026-09-10T15:57:02.275Z
---
Reconcile operator and RunPool architecture claims with production: pooled host admission raises on timeout; only direct scalar admitted_launch fails open. Remove misleading global-host/safety assurances and stale control defaults or lifecycle descriptions proven by the docs audit.
