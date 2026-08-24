---
type: is
id: is-01m0tt2515e5hd0p668h4cpqgp
title: Preserve required diamond edges beside finished collectors
kind: bug
status: in_progress
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - execution-model
dependencies: []
parent_id: is-01m0r93je6fk789d26aef6wx11
created_at: 2026-08-24T21:15:44.037Z
updated_at: 2026-08-24T21:23:50.705Z
---
GTIA v3.0-pre L0 exposed a dependency-propagation defect in src/metaproc/engine/graph.py. A consumer with both a required artifact/ref edge and a collect: downstream-step require: finished edge is allowed through when an upstream producer needed by the required artifact fails, because _requires_only_finished treats ancestry of the collected step as sufficient and ignores the separate required edge. Reproduce with a diamond graph, make failure propagation edge-aware, and prove the finished collector still runs for failures reachable only through its tolerant collected edge.

## Notes

L0 integration reproduction: analytical input closure failed, but promotion and review still ran because require: finished on a collected downstream step masked a separate required artifact edge in the same diamond. Added a failing pure graph regression, changed propagation to require every affected direct dependency to be a finished collector, and preserved the existing mixed-outcome replay behavior. Focused graph/replay tests: 44 passed. Full make verify: 4,352 passed, 8 skipped; lint, type, docs, browser, audits, distribution, and installed-wheel smoke all passed. One unrelated process-tree timing test failed on the first full run and passed both immediate focused rerun and the clean full rerun.
