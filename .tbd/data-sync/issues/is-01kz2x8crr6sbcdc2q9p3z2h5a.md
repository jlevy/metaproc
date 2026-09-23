---
type: is
id: is-01kz2x8crr6sbcdc2q9p3z2h5a
title: "PR #8 review MP8-04: reject stale pre-existing captured artifacts"
kind: bug
status: closed
priority: 1
version: 3
labels: []
dependencies: []
parent_id: is-01kz2x7xfhk0qsxn4ytw7et2bw
created_at: 2026-08-03T04:14:06.103Z
updated_at: 2026-08-03T04:20:25.417Z
closed_at: 2026-08-03T04:20:25.413Z
close_reason: Fixed with focused regression coverage; 96 related tests and Python lint/type checks pass.
---
src/metaproc/commands/run_process.py:1195. Final-response capture accepts any pre-existing frontmatter file as current tool output. Track pre-attempt identity and accept fallback only when the current attempt created or changed the target; add regression coverage.
