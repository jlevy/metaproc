---
type: is
id: is-01m0rrzsawhz8yhek3xk8xp3e7
title: "PR32 F6: deliver per-item force and three-view recovery consistency"
kind: task
status: closed
priority: 0
version: 4
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - execution-model
  - pr-review
dependencies: []
parent_id: is-01m0rm18400gvqf9d61s4138mg
created_at: 2026-08-24T02:18:29.084Z
updated_at: 2026-08-24T22:51:08.922Z
closed_at: 2026-08-24T02:40:19.775Z
close_reason: "Addressed in Metaproc PR #32 commits 7e8034d and 243d896. The plan now incorporates every F1-F8 correction, the disposition is posted at https://github.com/jlevy/metaproc/pull/32#issuecomment-5390055801, and canonical CI is green. Runtime implementation remains tracked by mp-p0sn, mp-zssw, mp-0ukj, mp-0cyw, mp-1af0, and mp-rrfn."
resolution: null
duplicate_of: null
---
Move within-scope per-item force into the first mapped-scope slice, propagate it into child execution, revalidate projected outputs on resume, and characterize consistency across parent task, child process, and child task completion views. Review: https://github.com/jlevy/metaproc/pull/32#issuecomment-5389812461

## Notes

2026-08-24 superseding implementation disposition: the pinned GTIA L0 proves that ordinary resume reruns only the failed mapped item while retaining completed siblings. The first mapped-scope slice therefore does not add qualified per-item or child-step force. Existing run-wide force and root skip remain; a narrower selector is deferred until operator evidence requires it. Publish this later rationale on the original PR32 review channel before merge.
