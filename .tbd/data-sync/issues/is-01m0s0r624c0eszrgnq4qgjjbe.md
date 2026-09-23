---
type: is
id: is-01m0s0r624c0eszrgnq4qgjjbe
title: Audit dormant retry-later recovery before adoption
kind: bug
status: open
priority: 1
version: 20
spec_path: docs/project/specs/active/plan-2026-09-10-runpool-execution-followups.md
delegate: codex
labels:
  - authentication
  - execution-model
dependencies: []
parent_id: is-01m260cd2wnmjm68zd3yqaw799
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
hold: paused
created_at: 2026-08-24T04:34:08.579Z
updated_at: 2026-09-10T18:45:37.293Z
---
Metaproc contains older wait, checkpoint, deferred-state, resume-daemon, and hard-coded fan-out cooling paths, but no current scheduler consumes a coherent public retry-later policy. Do not reintroduce the superseded proposal speculatively. Use released-consumer and framework-owned evidence to decide whether each primitive should be removed, retained as-is, or connected through the smallest shared policy. Retained behavior must avoid holding execution or host capacity while idle and reuse existing scheduler and checkpoint machinery.

## Notes

Excluded from the consolidated runtime pull request. Revisit only after framework-owned or downstream smoke evidence requires retry-later behavior; no speculative implementation is authorized.
