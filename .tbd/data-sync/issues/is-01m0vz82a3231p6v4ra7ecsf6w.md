---
type: is
id: is-01m0vz82a3231p6v4ra7ecsf6w
title: Upgrade Metaproc integration to Softschema 0.7.0
kind: task
status: in_progress
priority: 0
version: 2
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - dependency
  - tonight-2026-08-25
dependencies: []
parent_id: is-01m0vhs620ptcvxv074ccx88z4
created_at: 2026-08-25T08:05:35.170Z
updated_at: 2026-08-25T08:05:40.287Z
---
Adopt the audited first-party Softschema 0.7.0 release, migrate Metaproc's structured-failure adapter from engine validator keywords to stable error codes and property-complete locations, update the package constraint, exception cutoff/rationale, changelog, and lockfile, then pass focused compatibility tests and the full exact-head make verify gate.
