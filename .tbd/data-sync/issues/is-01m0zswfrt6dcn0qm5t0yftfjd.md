---
type: is
id: is-01m0zswfrt6dcn0qm5t0yftfjd
title: Persist a portable process-spec identity for cross-host hydrated run browsing
kind: task
status: open
priority: 2
version: 2
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - visualization
  - hydration
dependencies: []
parent_id: is-01m0rm18kbm24khxjemevb1ybv
created_at: 2026-08-26T19:48:50.586Z
updated_at: 2026-08-27T07:02:48.147Z
---
A run hydrated on another host can retain an absolute process_spec path that is unavailable under the local MetaBrowser root. Persist or otherwise resolve a safe repo-relative process identity so the runtime task/output table can load the exact plan after cross-host hydration. The current view fails safely with a typed warning; this must be proven before the cross-host browser gate.

## Notes

Commit 0af3967 makes the runtime task/output scanner portable for fully snapshotted runs without authored process files. This bead remains open only for full structural browser reconstruction, which still needs a safe portable process identity.
