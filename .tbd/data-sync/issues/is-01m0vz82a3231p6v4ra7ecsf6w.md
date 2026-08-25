---
type: is
id: is-01m0vz82a3231p6v4ra7ecsf6w
title: Upgrade Metaproc integration to Softschema 0.7.0
kind: task
status: closed
priority: 0
version: 4
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - dependency
  - tonight-2026-08-25
dependencies: []
parent_id: is-01m0vhs620ptcvxv074ccx88z4
created_at: 2026-08-25T08:05:35.170Z
updated_at: 2026-08-25T19:31:20.981Z
closed_at: 2026-08-25T19:31:20.980Z
close_reason: Softschema 0.7.0 migration, lock and cutoff documentation, structured-diagnostic adapter, focused coverage, and exact-head make verify all pass.
resolution: null
duplicate_of: null
---
Adopt the audited first-party Softschema 0.7.0 release, migrate Metaproc's structured-failure adapter from engine validator keywords to stable error codes and property-complete locations, update the package constraint, exception cutoff/rationale, changelog, and lockfile, then pass focused compatibility tests and the full exact-head make verify gate.
