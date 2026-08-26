---
type: is
id: is-01m0v08v74mc11m0s0bb7az8hw
title: "PR #34 I6: contain slot-binding failures; terminal state on retry exhaustion"
kind: bug
status: closed
priority: 1
version: 5
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies:
  - type: blocks
    target: is-01m0vhs620ptcvxv074ccx88z4
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-24T23:04:14.819Z
updated_at: 2026-08-25T19:28:30.034Z
closed_at: 2026-08-25T05:50:38.943Z
close_reason: null
resolution: null
duplicate_of: null
---
Merge blocker. (a) _bind_pool_dispatch raises CLIError inside the unguarded gather when run_dir is not under runs_dir — symlinked run dirs or relative RUNS_DIR now abort whole runs that previously completed, with siblings stuck running; degrade to step failure and resolve runs_dir once. (b) Pool exhaustion or auth-override on attempt >=2 returns early without mark_failed_at, leaving status.yaml running (the likely case: retry_exclude cools labels cumulatively); add terminal write + a retry-case test. (c) Gate the per-step scalar quota preflight behind refuse posture or hoist it run-level — it rglobs the runs tree over NFS per leaf and its warn verdict is discarded, on the shared sync_executor. Review: pull/34 comment (B1, Finding-3 retry, B2); holistic ledger #6.
