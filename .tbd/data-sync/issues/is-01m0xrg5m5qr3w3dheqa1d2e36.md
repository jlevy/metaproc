---
type: is
id: is-01m0xrg5m5qr3w3dheqa1d2e36
title: "PR #48: prove coverage of every retained superseded-stack behavior"
kind: task
status: closed
priority: 0
version: 5
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels: []
dependencies:
  - type: blocks
    target: is-01m0xrg6jeywxa1hwns3eay01m
parent_id: is-01m0xrg4vr6n4znzxz0kkxxxt7
created_at: 2026-08-26T00:46:09.540Z
updated_at: 2026-08-26T01:14:25.770Z
closed_at: 2026-08-26T01:14:25.770Z
close_reason: Completed the full superseded-stack comparison, accounted for old-only tests and documents, confirmed the consolidated branch retains the executable behavior, and recorded the public-safe evidence in the governing plan.
resolution: null
duplicate_of: null
---
Diff and review PRs #32, #33, #34, #35, #37, #43, and #47 against #48. Map each retained behavior and regression test, explicitly justify exclusions, and create child bugs for any uncovered behavior.

## Notes

Compared the superseded implementation stack with the consolidation: 90 common code/test/docs files, the consolidated tree adds focused coordinator coverage, and every old-only test name is either superseded by prerequisite coverage or renamed without behavioral loss. Final mapping will be recorded in the plan before closure.
