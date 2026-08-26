---
type: is
id: is-01m0zfmp3h1fefdr5bc88zp9c8
title: Rewrite the README Documentation section
kind: task
status: open
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies: []
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-26T16:49:49.169Z
updated_at: 2026-08-26T16:56:15.846Z
---
Present the three-document reading path first, in order: (1) metaproc-concepts-and-principles.md - vocabulary, ownership boundaries, step modes, optimization loops; everything else assumes it and the design doc says so ('read it first for the definitions assumed below'). (2) metaproc-design.md - how the system is actually built; the document whose section-5 numbering exists because its first four sections became doc 1. (3) process-framework-concepts.md - the general model plus the map of how Metaproc instantiates it; a reference, not a prerequisite. Then invert Start Here so documents are rows and 'metaproc help <topic>' is an annotation column - today the command is the row and the doc is a parenthetical (source), hiding 12750 words from anyone reading on GitHub. Add a Project Documentation section linking the design doc, proposals doc, arch index, and project records directly. Link docs/project/releases/ and state its relationship to CHANGELOG.md. The design doc must stay prominent even though it moves under docs/project: filing it as a project record must not bury the second doc a contributor should read.
