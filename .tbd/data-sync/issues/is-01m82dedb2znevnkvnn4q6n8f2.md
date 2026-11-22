---
type: is
id: is-01m82dedb2znevnkvnn4q6n8f2
title: Retain ownership when a RunPool launch is cancelled repeatedly
kind: bug
status: in_progress
priority: 1
version: 3
labels: []
dependencies:
  - type: blocks
    target: is-01m82bevyb6z4vh6n884bc6a9x
parent_id: is-01m82be4yjpzsaestvwb057vxx
created_at: 2026-11-22T19:59:02.486Z
updated_at: 2026-11-22T20:00:07.552Z
---
The release gate caught a first child PID with no process_start before pool_shutdown and the child still present at return. Cancellation of the submit Future and immediate pool shutdown can interrupt late process cleanup. Reproduce deterministically, keep the hard shutdown bound and stable event/accounting behavior, review and verify before release.
