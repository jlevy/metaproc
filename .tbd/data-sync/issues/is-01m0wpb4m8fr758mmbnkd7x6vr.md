---
type: is
id: is-01m0wpb4m8fr758mmbnkd7x6vr
title: Persist code-step failure state and diagnostics in run-process
kind: bug
status: closed
priority: 0
version: 8
spec_path: null
labels:
  - live-smoke
  - observability
dependencies: []
parent_id: is-01m0r92q2y1pe7dmhrcj6nst7q
created_at: 2026-08-25T14:49:13.095Z
updated_at: 2026-08-25T17:01:14.103Z
closed_at: 2026-08-25T17:01:14.102Z
close_reason: A framework-owned deterministic failure-path regression now proves durable nonempty failed status and diagnostics, with CLI, status, trace, tail, and events in agreement. Full verification passed; the fix remains subject to consolidated review.
resolution: null
duplicate_of: null
---
A deterministic code handler can fail while the CLI exits nonzero and the trace records an error, yet process events carry an empty reason and status projects the run as complete with a missing step. Reproduce with a framework-owned exception, persist a sanitized actionable failure reason and terminal failed state, make status, trace, tail, events, and CLI agree, and cover both targeted-step and ordinary dependency-blocking execution.

## Notes

Precommit review found no unresolved issues after correcting condition precedence and partial-resume failure projection. Full verification passed. The implementation remains part of the consolidated runtime-fix review and is not independently authorized to merge.
