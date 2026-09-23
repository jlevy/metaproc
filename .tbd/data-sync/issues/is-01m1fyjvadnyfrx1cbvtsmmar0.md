---
type: is
id: is-01m1fyjvadnyfrx1cbvtsmmar0
title: Implement Safeproc safety models, policy, journal, and replay
kind: task
status: closed
priority: 1
version: 4
spec_path: docs/project/specs/active/plan-2026-09-01-safeproc-local-incubation.md
labels: []
dependencies:
  - type: blocks
    target: is-01m1fyjvnjysz23ax8tpad21a2
parent_id: is-01m1fxnwnyqvq1gg8ak7317kyc
created_at: 2026-09-02T02:18:48.524Z
updated_at: 2026-09-02T04:12:20.540Z
closed_at: 2026-09-02T04:12:20.540Z
close_reason: models, policy engine, identity, clocks, journal, and replay implemented with 93 passing tests on Linux; replay reproduces live decisions and detects drift. Branch claude/safeproc-incubation.
resolution: null
duplicate_of: null
---
Implement neutral clocks, process identities, scoped host samples, resource profiles, pure admission and pressure policy, versioned redacted journal records, and deterministic replay with memory-guard and independently translated Procguard cases.
