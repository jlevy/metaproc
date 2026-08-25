---
type: is
id: is-01m0s0r624c0eszrgnq4qgjjbe
title: Audit dormant retry-later recovery before adoption
kind: bug
status: in_progress
priority: 1
version: 16
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
delegate: codex
labels:
  - authentication
  - execution-model
dependencies: []
parent_id: is-01m0rs7df0g28zgnsykar366kb
child_order_hints:
  - is-01m0s7cvkpk6pmhzenf9stzmgr
  - is-01m0s7cw0ghtj387wj4nar45we
  - is-01m0s7cwczrk6a655t1gjxtfnj
  - is-01m0s7cwsmee3fj0gehfkfz329
  - is-01m0s7cx65x3r3cdj4j0aq4tax
  - is-01m0s7cxknep5425wm4z90bcqj
  - is-01m0s7cy280m06mznmrggw55ka
  - is-01m0s7nnvzzas4frtb9y5vcexj
  - is-01m0t8070qf1kded17fc1tjya3
  - is-01m0t808bang7nryyzzhtg6phy
  - is-01m0tjefebxck534azhjgd5ew9
created_at: 2026-08-24T04:34:08.579Z
updated_at: 2026-08-25T16:59:51.469Z
---
Metaproc contains older wait, checkpoint, deferred-state, resume-daemon, and hard-coded fan-out cooling paths, but no current scheduler consumes a coherent public retry-later policy. Do not reintroduce the superseded proposal speculatively. Use released-consumer and framework-owned evidence to decide whether each primitive should be removed, retained as-is, or connected through the smallest shared policy. Retained behavior must avoid holding execution or host capacity while idle and reuse existing scheduler and checkpoint machinery.

## Notes

PR #36 retry transport was deleted after review and is being closed as superseded. Its independent cloud auth_policy transport fix moved to PR #34 at 3d11a64. Follow-on implementation beads are paused until this audit produces evidence; tracking them does not authorize implementation.
