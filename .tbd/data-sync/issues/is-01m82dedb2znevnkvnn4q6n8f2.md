---
type: is
id: is-01m82dedb2znevnkvnn4q6n8f2
title: Retain ownership when a RunPool launch is cancelled repeatedly
kind: bug
status: in_progress
priority: 1
version: 4
labels: []
dependencies:
  - type: blocks
    target: is-01m82bevyb6z4vh6n884bc6a9x
parent_id: is-01m82be4yjpzsaestvwb057vxx
created_at: 2026-11-22T19:59:02.486Z
updated_at: 2026-11-22T20:11:18.562Z
---
The release gate caught a first child PID with no process_start before pool_shutdown and the child still present at return. Cancellation of the submit Future and immediate pool shutdown can interrupt late process cleanup. Reproduce deterministically, keep the hard shutdown bound and stable event/accounting behavior, review and verify before release.

## Notes

Frozen at 81191a0. Deterministic late and active launch tests reproduced the interrupted kill. The fix preserves a single normal cancellation for owned submissions, immediate cancellation of borrowed code tasks, and forced cancellation at the hard deadline. Five focused checks passed on the frozen implementation. Exact-head senior, security, and correctness reviews found no open release-diff issue. Complete verify is running.
