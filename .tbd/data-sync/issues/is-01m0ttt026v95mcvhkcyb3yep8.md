---
type: is
id: is-01m0ttt026v95mcvhkcyb3yep8
title: Keep fan-out fingerprints stable across lazy item discovery
kind: bug
status: closed
priority: 1
version: 6
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - execution-model
dependencies: []
parent_id: is-01m0r93je6fk789d26aef6wx11
created_at: 2026-08-24T21:28:45.381Z
updated_at: 2026-08-25T19:31:21.223Z
closed_at: 2026-08-25T19:31:21.223Z
close_reason: Runtime-discovered items are excluded from definition fingerprints; generated-roster status and resume coverage pass.
resolution: null
duplicate_of: null
---
A generated roster can make status mark completed mapped parents stale because execution fingerprints the initial empty discovery set while status fingerprints discovered runtime items. Runtime discovery data is not step-definition identity. Exclude discovered items and filtered_count from definition fingerprints while preserving authored fan-out semantics, and prove current status plus failed-item-only resume with a generated roster.

## Notes

The regression excludes only runtime-discovered items and filtered_count; authored fan-out changes still invalidate the hash. Keep open until the clean consolidated head passes focused fingerprint, dependency, status, and resume coverage.
