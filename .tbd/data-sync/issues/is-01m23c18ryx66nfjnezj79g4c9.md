---
type: is
id: is-01m23c18ryx66nfjnezj79g4c9
title: Make fail_run stop the run, not just fail its item
kind: bug
status: open
priority: 2
version: 3
spec_path: docs/project/specs/active/plan-2026-09-10-runpool-execution-followups.md
labels:
  - contract-failure
dependencies:
  - type: blocks
    target: is-01m260z1v7cj4wtcghqr3gvn0k
parent_id: is-01m269mehrtqg1chz8dqsfzhwr
created_at: 2026-09-09T15:19:26.749Z
updated_at: 2026-09-10T18:47:04.397Z
---
Phase 2 remnant of the contract-failure-primitives spec, verified still open at v0.4.0. src/metaproc/engine/retry.py:403 says so in its own comment: nothing stops a run on this yet, so a fail_run output currently fails its item like an ordinary 'fail'. The action is accepted and threaded (retry.py:306 OutputFailureAction, :329-330, :378-381) but the run-level effect is missing. Until then, authoring on_invalid: fail_run promises containment the engine does not deliver. Was tracked only as a spec checkbox, so it never appeared in tbd ready.
