---
type: is
id: is-01m0t5d44v9sfzcegwcth6e1b4
title: "Review PR #35: retain lifecycle ownership through cleanup"
kind: task
status: closed
priority: 1
version: 18
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
child_order_hints:
  - is-01m0t7zzb7md36w7w0jnadbcts
  - is-01m0t7zzvad31qstfnwq1hz6xf
  - is-01m0t800djjz7ay06rw10w40g2
  - is-01m0t8010r1ffa728245ms93w5
  - is-01m0t801gh2mz2b7vr19tg3mnj
  - is-01m0t8020a7b1mvxfhgsp7ca6n
  - is-01m0t802h83jke86ayjg1p1jd7
  - is-01m0t8031yzxzk5tff9vfc5gd2
  - is-01m0t803hh1fxvg281pqt5ceay
  - is-01m0tcp77xca1zzp25pvq4x43y
  - is-01m0tcw3acmhtkj35g8thzxrmd
  - is-01m0td3dehfnryederv1dpm7f3
created_at: 2026-08-24T15:14:43.482Z
updated_at: 2026-08-24T22:32:05.604Z
closed_at: 2026-08-24T17:54:41.745Z
close_reason: "Fixed in e233ec1. Per-finding disposition: https://github.com/jlevy/metaproc/pull/35#issuecomment-5399129306. Local make verify passed (4,339 passed, 8 skipped); GitHub CI run 32759134105 passed lint, distribution, and Python 3.12/3.13/3.14."
resolution: null
duplicate_of: null
---
Senior review of #35 (codex/gtia-v3-cancellation-safety). Touches resource_sampling and runpool backend: check whether the synchronous run_sampled_step_command event-loop block (finding F3c) is fixed, cancellation/cleanup ownership is correct, no orphaned process trees. Post review comment; follow up before merge.

## Notes

ROUND 2 (2026-08-24, head 0e0d3e3): https://github.com/jlevy/metaproc/pull/35#issuecomment-5402358872 — BLOCKER + both HIGHs genuinely FIXED (env=prepared.env both paths with child-side test; ownership fence; kill() never raises). Ctrl-C wired with real SIGINT end-to-end test. Strongest test work in the stack. NEW must-fix: N1 synthesized CancelledError escapes run_parallel.py:2027 'except Exception' on pool kill → leaked credential lease; N2 shutdown() lost every deadline (status/log tail now behind unbounded gather — same wedged-kill failure relocated); F2 _active no longer popped in finally. Should: N3 unbounded _observed_descendants re-walked at 10Hz; N4 <=10s unobserved-descendant window; N5 cancelled poisons later partial runs; N6 kill sentinel → retry churn.
