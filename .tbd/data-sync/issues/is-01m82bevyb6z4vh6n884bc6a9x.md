---
type: is
id: is-01m82bevyb6z4vh6n884bc6a9x
title: Run candidate verification and certify the release commit
kind: task
status: closed
priority: 1
version: 5
labels: []
dependencies:
  - type: blocks
    target: is-01m82beyvkx5hang2z1kt6zr4j
parent_id: is-01m82be4yjpzsaestvwb057vxx
created_at: 2026-11-22T19:24:20.297Z
updated_at: 2026-11-22T20:45:28.792Z
closed_at: 2026-11-22T20:45:28.792Z
close_reason: "Merged in PR #129 after senior, security and correctness review plus complete local verification. Exact-main CI run 45067836697 passed all four jobs on f6697c9. The timing-sensitive local tests all passed in their own lane."
resolution: null
duplicate_of: null
---
Run make verify, inspect packaged artifacts, watch PR/main CI, and confirm the immutable source commit.

## Notes

Exact 81191a0 passed local make verify using complete serial and parallel partitions: 6090 tests passed and 15 skipped. Native YAML lane 127 passed, audits found zero known vulnerabilities, built wheel and sdist inspection and isolated wheel smoke passed. Three exact-head reviews posted on #129. Hosted PR and exact main checks pending.
