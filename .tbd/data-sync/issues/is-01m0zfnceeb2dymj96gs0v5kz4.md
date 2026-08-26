---
type: is
id: is-01m0zfnceeb2dymj96gs0v5kz4
title: Mark commit and fencing as modeled but not implemented
kind: task
status: open
priority: 2
version: 1
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
  - terminology
dependencies: []
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-26T16:50:12.046Z
updated_at: 2026-08-26T16:50:12.046Z
---
commit: 17 uses in the general doc as a core object ('the single durable fact that a task published a complete, validated set of outputs'); the one hit in the shipped doc is 'git commit'. fencing: 3 vs 0. The general doc's own deviations list already says Metaproc has no single commit record covering a multi-output task and nothing fences a late stale attempt - the shipped glossary should say so too.
