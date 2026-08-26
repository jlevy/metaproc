---
type: is
id: is-01m0xrg688teem49wpyndafae4
title: "PR #48: rerun exact-head framework verification after baseline integration"
kind: task
status: closed
priority: 0
version: 6
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels: []
dependencies:
  - type: blocks
    target: is-01m0xrg6jeywxa1hwns3eay01m
parent_id: is-01m0xrg4vr6n4znzxz0kkxxxt7
created_at: 2026-08-26T00:46:10.183Z
updated_at: 2026-08-26T01:50:37.786Z
closed_at: 2026-08-26T01:50:37.784Z
close_reason: All local exact-head and GitHub CI verification gates pass on f94b8a98.
resolution: null
duplicate_of: null
---
Run focused regression suites and complete make verify on the integrated exact head, then require all GitHub CI jobs to pass across supported Python versions and distribution checks.

## Notes

Final exact head f94b8a98 passed make verify after rebasing directly onto merged PR #44/main: 4,431 passed, 8 skipped; lint, types, docs, public hygiene, supply-chain checks, browser checks, audits, build, distribution validation, and installed-wheel smoke pass. The pre-push hook repeated the same gate. GitHub CI passes lint, distribution, and Python 3.12, 3.13, and 3.14.
