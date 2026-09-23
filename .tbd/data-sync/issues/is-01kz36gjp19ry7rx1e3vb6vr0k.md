---
type: is
id: is-01kz36gjp19ry7rx1e3vb6vr0k
title: "PR #9 review PR9-R2: recognize legal wide typed run IDs"
kind: bug
status: closed
priority: 2
version: 3
labels:
  - pr-review
  - pr-9
dependencies: []
parent_id: is-01kz36g3q9wbmhwnwcs170y1s3
created_at: 2026-08-03T06:55:51.486Z
updated_at: 2026-08-03T06:59:38.833Z
closed_at: 2026-08-03T06:59:38.832Z
close_reason: "Fixed: status now recognizes an exact registered run ID before applying the legacy 63-character heuristic. Added a maximum-width typed run regression; all 16 TestLooksLikeRunId cases pass."
---
Formal review PR9-R2 (Medium), PR #9. src/metaproc/commands/status.py:113. The 63-character historical label heuristic runs before typed-ID recognition, rejecting legal 256-bit typed run IDs. Recognize registered run IDs first; apply the length limit only to legacy heuristic IDs. Add maximum-width regression coverage.
