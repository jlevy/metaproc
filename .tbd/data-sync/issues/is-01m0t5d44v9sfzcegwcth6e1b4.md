---
type: is
id: is-01m0t5d44v9sfzcegwcth6e1b4
title: "Review PR #35: retain lifecycle ownership through cleanup"
kind: task
status: in_progress
priority: 1
version: 4
labels:
  - pr-review
dependencies: []
created_at: 2026-08-24T15:14:43.482Z
updated_at: 2026-08-24T22:31:53.993Z
---
Senior review of #35 (codex/gtia-v3-cancellation-safety). Touches resource_sampling and runpool backend: check whether the synchronous run_sampled_step_command event-loop block (finding F3c) is fixed, cancellation/cleanup ownership is correct, no orphaned process trees. Post review comment; follow up before merge.

## Notes

ROUND 2 (2026-08-24, head 0e0d3e3): https://github.com/jlevy/metaproc/pull/35#issuecomment-5402358872 — BLOCKER + both HIGHs genuinely FIXED (env=prepared.env both paths with child-side test; ownership fence; kill() never raises). Ctrl-C wired with real SIGINT end-to-end test. Strongest test work in the stack. NEW must-fix: N1 synthesized CancelledError escapes run_parallel.py:2027 'except Exception' on pool kill → leaked credential lease; N2 shutdown() lost every deadline (status/log tail now behind unbounded gather — same wedged-kill failure relocated); F2 _active no longer popped in finally. Should: N3 unbounded _observed_descendants re-walked at 10Hz; N4 <=10s unobserved-descendant window; N5 cancelled poisons later partial runs; N6 kill sentinel → retry churn.
