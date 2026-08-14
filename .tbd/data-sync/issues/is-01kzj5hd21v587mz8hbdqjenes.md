---
type: is
id: is-01kzj5hd21v587mz8hbdqjenes
title: Wire psutil sampling into step execution
kind: bug
status: closed
priority: 1
version: 5
labels: []
dependencies: []
created_at: 2026-08-09T02:27:26.400Z
updated_at: 2026-08-09T03:05:47.595Z
closed_at: 2026-08-09T03:05:47.594Z
close_reason: "Implemented and verified in combined draft PR #15: code-step CPU/RSS sampling, step lifecycle resource events, and registered ResourcesDocument/0.1 with strict V1/V2 compatibility. Local make verify and the complete GitHub CI matrix pass."
---
Runtime resource sampling exists but no code-step execution path enters PsutilSampler, so code steps do not persist CPU and RSS samples to the run resource ledger. Wire the three supported code-mode execution sites and prove root/nested attribution with focused tests.

## Notes

The handoff summary says code-step and agent-step paths, but its detailed operational-visibility spec explicitly scopes this change to the three code-mode sites: run-process, run-step, and run-parallel. Agent transcripts contribute token/tool evidence, but agent-process CPU/RSS sampling is not one of the enumerated implementation sites or acceptance items; this bead follows the detailed spec and records that wording discrepancy for handoff.
