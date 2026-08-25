---
type: is
id: is-01m0x587ksrhw98madas4v2gt2
title: "R12: drain a cancelled pooled scalar before releasing outer ownership"
kind: bug
status: closed
priority: 0
version: 3
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0x358va0njc6k4g00pccj7e
created_at: 2026-08-25T19:09:46.471Z
updated_at: 2026-08-25T19:25:28.550Z
closed_at: 2026-08-25T19:25:28.549Z
close_reason: Fixed with shielded per-submission cancel-and-drain ownership; real subprocess cleanup precedes outer resource release.
resolution: null
duplicate_of: null
---
A scalar leaf awaits the Future returned by the shared RunPool. Task cancellation currently cancels that Future but not the pool submission, so the scalar executor can unwind credential, host, and leaf ownership while the subprocess is still running; only later top-level pool shutdown kills it. Add a per-submission cancel-and-drain primitive, shield the scalar Future, and prove the process is terminal before credential teardown and outer capacity release.

## Notes

Fixed: RunPool exposes per-submission cancel-and-drain ownership; scalar callers shield the result and drain the owned task/process before credential, host, or leaf release. The real-process regression passes.
