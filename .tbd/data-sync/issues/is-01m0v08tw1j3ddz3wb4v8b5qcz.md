---
type: is
id: is-01m0v08tw1j3ddz3wb4v8b5qcz
title: "PR #33 I5: size the run executor deliberately; fix ceiling docs"
kind: bug
status: open
priority: 1
version: 2
labels:
  - pr-review
dependencies:
  - type: blocks
    target: is-01m0vhs620ptcvxv074ccx88z4
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-24T23:04:14.465Z
updated_at: 2026-08-25T04:10:15.999Z
---
Merge blocker. RunExecutionContext.create builds ThreadPoolExecutor with no max_workers → min(32,cpus+4) silently FLOORS --max-concurrency (measured 14 executing of 40 requested on 10 CPUs; 8 on a 4-vCPU orchestrator) while four doc surfaces still call that flag the run-wide leaf ceiling; queued leaves hold a permit and report running. Also: close(wait=False) releases the run lease before run-owned threads stop (regression vs main's bounded 300s join) — fix or document; drop/rework the two unreachable bare-CancelledError raises in _leaf_slot; CHANGELOG says fan-outs only and 'bounded by run and step ceilings'. Review: pull/33 comment (N1/N1b/N1c, N2, N3); holistic ledger #5.
