---
type: is
id: is-01m0zfmp3h1fefdr5bc88zp9c8
title: "Rewrite the README Documentation section: every doc listed, with its CLI equivalent"
kind: task
status: closed
priority: 1
version: 4
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies: []
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-26T16:49:49.169Z
updated_at: 2026-08-27T15:07:49.271Z
closed_at: 2026-08-27T15:07:49.271Z
close_reason: Implemented in the documentation reorganization (phases 1-6).
resolution: null
duplicate_of: null
---
Phase 2. Two rules applied to every table in the section.

RULE 1 - the document is the row, the command is a column. Today rows 89-91 make 'metaproc help concepts' the row and the file a parenthetical (source). Invert to: | Document | metaproc help | Purpose |, with the document linked to its path and the topic name in the middle column. Applies to all 15 shipped docs.

RULE 2 - every first-party document appears somewhere. The audit found these linked from nowhere: six of eight arch docs (only arch-metaproc-core and arch-testing are linked), docs/releases/ (3 files), and src/metaproc/runpool/README.md.

Sections to produce:
- Start Here: the three-document reading path in order - concepts, design, framework - one line each on what it answers and whether it is required or optional.
- Reference: conventions, artifacts, execution-model, pricing, CHANGELOG.
- Architecture: all seven arch-* docs with their topic names.
- Runbooks: unchanged.
- Project Records: docs/project/ and docs/releases/, with the relationship to CHANGELOG.md stated.

Every row that has a 'metaproc help' topic names it.
