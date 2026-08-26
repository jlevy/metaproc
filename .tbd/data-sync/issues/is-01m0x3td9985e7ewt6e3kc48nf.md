---
type: is
id: is-01m0x3td9985e7ewt6e3kc48nf
title: "R5: reject unsupported mapped-worker topology before executing steps"
kind: bug
status: closed
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0x358va0njc6k4g00pccj7e
created_at: 2026-08-25T18:44:44.968Z
updated_at: 2026-08-25T19:25:26.056Z
closed_at: 2026-08-25T19:25:26.055Z
close_reason: Fixed with pre-execution topology validation and CLI coverage.
resolution: null
duplicate_of: null
---
A mapped composite rejects gcp-worker only inside the step executor. Earlier DAG levels may execute before the run reaches that incompatibility, and dry-run does not surface it. Validate the active plan and backend before any execution or cloud dispatch, with a CLI regression proving no earlier step runs.

## Notes

Fixed: active-plan topology validation rejects mapped composite gcp-worker execution before any DAG step or cloud dispatch.
