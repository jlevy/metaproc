---
type: is
id: is-01m0s7cy280m06mznmrggw55ka
title: Route quota preflight refusal through retry-later policy
kind: bug
status: open
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - authentication
  - execution-model
dependencies: []
parent_id: is-01m0s0r624c0eszrgnq4qgjjbe
created_at: 2026-08-24T06:30:19.976Z
updated_at: 2026-08-24T07:23:17.291Z
---
The authentication architecture says refuse posture consults fail-fast, wait, or signal, but run_parallel always raises CLIError. Apply the same typed retry-later decision to preflight refusal with deterministic tests and no execution attempt.

## Notes

Route the existing quota-preflight refusal through the shared RetryLaterPolicy decision. Reuse dispatch/preflight.py and the existing retry-later primitives; do not add a GTIA-specific gate.
