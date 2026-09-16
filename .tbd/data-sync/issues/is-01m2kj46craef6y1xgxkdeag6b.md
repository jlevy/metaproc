---
type: is
id: is-01m2kj46craef6y1xgxkdeag6b
title: "PR #83 review P2-R1: leaf host limit follows the pool ceiling, not the leaf ceiling"
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
parent_id: is-01m2kj39h261sajhsakeg5hhh1
created_at: 2026-09-15T22:13:44.983Z
updated_at: 2026-09-15T22:13:44.983Z
---
PR #83 part 2 R1 (Medium). A max_concurrency_hint below --max-concurrency makes queued leaves wait 60 s and bypass host admission. commands/run_process.py:411, 2546-2551, 327-329; runpool/scalar_admission.py:54-73.
