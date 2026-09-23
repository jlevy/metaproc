---
type: is
id: is-01m0tx8jrdfkjcr0n1v2b1qs9q
title: "Hold PR #38 out of 0.3.0: it removes public CLI surfaces"
kind: task
status: closed
priority: 1
version: 5
labels:
  - release,scope
dependencies: []
parent_id: is-01m0tx34t3n8g39jjbhzdrrpwf
created_at: 2026-08-24T22:11:40.429Z
updated_at: 2026-08-25T17:01:14.850Z
closed_at: 2026-08-25T17:01:14.849Z
close_reason: Held as planned. Pull request 38 remained outside 0.3.0 so its public CLI removals and migration story could be reviewed independently.
resolution: null
duplicate_of: null
---
Keep pull request 38 out of the current release because it removes documented public CLI surfaces and needs its own migration story. Ship the stable main-line release first, then review the cleanup and mapped-scope runtime work independently. This preserves a clear rollback and bisect boundary.
