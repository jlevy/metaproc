---
type: is
id: is-01m10nrksafes6r6q5e02pk4sx
title: Narrow run-plan snapshots to non-sensitive projection authority
kind: bug
status: closed
priority: 0
version: 2
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - review
  - runtime-projection
dependencies: []
parent_id: is-01m10mm4vpgbqgrjqx4dbjee41
created_at: 2026-08-27T03:56:03.754Z
updated_at: 2026-08-27T04:36:02.239Z
closed_at: 2026-08-27T04:36:02.239Z
close_reason: "Fixed in 9d34c1f; full make verify passed with 4,493 tests and GitHub CI completed 5/5 green. Published per-finding dispositions on PR #49."
resolution: null
duplicate_of: null
---
A review found that persisting the complete resolved Plan would copy opaque adapter config, environment, parameters, and fan-out item records into every run. Replace it with a minimal DTO containing scope identity and per-step ID, composite/fan-out shape, declared output ports, and fingerprint. Prove sentinel config and fan-out payloads are not serialized.
