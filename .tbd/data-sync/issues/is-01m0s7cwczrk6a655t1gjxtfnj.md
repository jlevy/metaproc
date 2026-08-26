---
type: is
id: is-01m0s7cwczrk6a655t1gjxtfnj
title: Persist retry-later checkpoint retry state and readiness
kind: bug
status: open
priority: 1
version: 7
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - authentication
  - execution-model
dependencies: []
parent_id: is-01m0s0r624c0eszrgnq4qgjjbe
hold: paused
created_at: 2026-08-24T06:30:18.270Z
updated_at: 2026-08-25T19:29:03.131Z
---
resume-daemon currently logs that it increments retries_attempted after exit 78 but does not rewrite the checkpoint, so max_retries cannot terminate repeated deferral. It also ignores next_attempt_earliest_ts. Persist each re-deferral atomically and honor the explicit readiness timestamp, including indefinite-cooling cases.

## Notes

Reuse and harden the existing retry_later checkpoint, event, deferred-state, and resume-daemon stack. Preserve version-1 compatibility; do not introduce a second checkpoint protocol or resume service.
