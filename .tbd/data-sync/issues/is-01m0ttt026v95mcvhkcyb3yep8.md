---
type: is
id: is-01m0ttt026v95mcvhkcyb3yep8
title: Keep fan-out fingerprints stable across lazy item discovery
kind: bug
status: in_progress
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - execution-model
dependencies: []
parent_id: is-01m0r93je6fk789d26aef6wx11
created_at: 2026-08-24T21:28:45.381Z
updated_at: 2026-08-24T22:09:29.555Z
---
The GTIA v3.0-pre L0 smoke completes successfully, then metaproc status immediately marks every mapped composite parent stale. The execution-time step hash is computed from the initial plan before a generated roster exists (fan_out.items=[]), while status rebuilds the plan after the roster exists and includes discovered items in fingerprint_step. Runtime discovery data is not step-definition identity. Exclude discovered items and filtered_count from definition fingerprints while preserving authored fan-out semantics, and prove status/current plus failed-item-only resume with a generated roster.

## Notes

TDD fix excludes only runtime-discovered fan_out.items and filtered_count from step-definition fingerprints; authored fan-out changes still invalidate the hash. Focused fingerprint/dependency/status/mapped/context suite: 177 passed. Full make verify: 4,354 passed, 8 skipped, with lint, types, docs, audits, distribution, and installed-wheel smoke green. Keep open until a fresh pinned GTIA L0 reports every mapped parent current and passes failed-item-only resume.
