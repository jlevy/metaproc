---
type: is
id: is-01m0typv63sjc07enhafsqdwfv
title: "PR #38 review 1: reject nonexistent status paths instead of false success"
kind: bug
status: closed
priority: 1
version: 3
labels: []
dependencies: []
parent_id: is-01m0typ5swnqc9v7gee2ymkjs9
created_at: 2026-08-24T22:36:56.387Z
updated_at: 2026-08-24T22:56:48.482Z
closed_at: 2026-08-24T22:56:48.482Z
close_reason: Fixed in 809fccc; exact-head CI run 32786763844 passed all five jobs and disposition published at issuecomment-5402607487.
resolution: null
duplicate_of: null
---
Review finding 1 at issuecomment-5402359572. src/metaproc/commands/status.py:388 and related filesystem status surfaces accept a nonexistent run-id-shaped path and can report an all-zero run as complete. Add an existence guard with an actionable error and cover the false-success path.
