---
type: is
id: is-01m3603eez0gfd4yypa3vggpbh
title: "PR 96 R1: enforce input identity across single-step and composite execution"
kind: bug
status: open
priority: 1
version: 1
labels: []
dependencies: []
parent_id: is-01m35zgrcms4fcyvf39xd6wr4j
created_at: 2026-09-23T02:04:20.315Z
updated_at: 2026-09-23T02:04:20.315Z
---
Design review of https://github.com/jlevy/metaproc/pull/96 at b2be344. Reproduced run-step accepting ds-2 and writing completed output inside a run whose identity config remains ds-1; a subsequent normal resume retains mixed outputs. Also reproduced a child identity input changing through a parent literal with binding without a guard. Centralize input-change enforcement at the execution/scope boundary, or explicitly reject unsupported declaration contexts. Preserve run and scope bindings across all supported entry points.
