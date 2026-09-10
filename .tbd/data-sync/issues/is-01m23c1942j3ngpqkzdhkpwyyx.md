---
type: is
id: is-01m23c1942j3ngpqkzdhkpwyyx
title: Let a plugin register an OutputFailure classifier
kind: feature
status: open
priority: 3
version: 2
spec_path: docs/project/specs/active/plan-2026-09-10-runpool-execution-followups.md
labels:
  - contract-failure
dependencies: []
parent_id: is-01m269mehrtqg1chz8dqsfzhwr
created_at: 2026-09-09T15:19:27.106Z
updated_at: 2026-09-10T18:47:05.157Z
---
Phase 2 remnant of the contract-failure-primitives spec, verified still open at v0.4.0: no plugin registration hook for a classifier receiving an OutputFailure and returning a label exists anywhere in src/metaproc/. Consumers cannot extend failure classification without patching core, which cuts against the framework's consumer-agnostic boundary. Was tracked only as a spec checkbox.
