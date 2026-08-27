---
type: is
id: is-01m12bcfgxy3a4x8vt0svyvf0s
title: Polish the README doc index and rename the concepts docs
kind: task
status: open
priority: 1
version: 1
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies: []
created_at: 2026-08-27T19:33:09.277Z
updated_at: 2026-08-27T19:33:09.277Z
---
Follow-up review of PR #51 by the user. Steps:
1. README: real doc titles as bold labels for Essential/Operator/Reference; slugs for Architecture and Project Docs. DONE
2. README: apply common-doc-guidelines; remove em dashes from the sections. DONE
3. Rename src/metaproc/docs/metaproc-concepts-and-principles.md -> metaproc-concepts.md, retitle 'Metaproc Concepts'.
4. Rename src/metaproc/docs/process-framework-concepts.md -> process-framework-theory.md, retitle 'Process Framework Theory'; README presents it as background.
5. Sweep every reference: topic registry doc fields, HelpTopics default, check_distribution suffixes, sibling links in shipped docs, README, development.md, docs/project/, spec links, code docstrings (scalar_admission.py), skill regen.
6. make verify; commit; push to PR #51.
