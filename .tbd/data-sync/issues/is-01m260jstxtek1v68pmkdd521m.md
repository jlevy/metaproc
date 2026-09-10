---
type: is
id: is-01m260jstxtek1v68pmkdd521m
title: "PR 75 F1: retain diagnostics across typed RunPool event reads"
kind: bug
status: closed
priority: 1
version: 5
spec_path: docs/project/specs/active/plan-2026-09-10-runpool-execution-followups.md
labels: []
dependencies: []
parent_id: is-01m260cd2wnmjm68zd3yqaw799
created_at: 2026-09-10T15:57:01.659Z
updated_at: 2026-09-10T18:46:59.640Z
closed_at: 2026-09-10T16:23:20.342Z
close_reason: Review and bounded fixes complete; full design review published on PR 75 with per-finding dispositions. Six lifecycle regressions reproduced failures before repair; make verify passed (4622 passed, 8 skipped), including audits and distribution smoke. Commit/push/CI remain tracked by mp-5v99.
resolution: null
duplicate_of: null
---
Typed readers drop controller fields from concurrency_adjust/pressure_check and skip quota_pause_started/tick/resumed plus health_sample. Add current writer fields and event models without changing existing JSON, with writer-to-reader regression coverage.

## Notes

Added full typed event round trips and per-tick hysteresis. Focused tests passed; full-suite golden snapshots require only two intentional hysteresis fields per pressure sample. Updating those snapshots and simplifying redundant test assertions before final verify.
