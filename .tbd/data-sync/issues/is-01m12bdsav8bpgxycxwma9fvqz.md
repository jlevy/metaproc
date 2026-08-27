---
type: is
id: is-01m12bdsav8bpgxycxwma9fvqz
title: Polish the README doc index and rename the concepts docs
kind: task
status: closed
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies: []
created_at: 2026-08-27T19:33:52.090Z
updated_at: 2026-08-27T19:39:55.729Z
closed_at: 2026-08-27T19:39:55.728Z
close_reason: "Done. Renames via repren (2 files, all references swept), README regrouped per review: Essential four docs in reading order, Architecture adjacent, Operator Runbooks, theory under Reference as background. Bold real titles for main docs, slugs elsewhere, pipe separators in Commands, em dashes removed. make verify green (4,449 passed)."
resolution: null
duplicate_of: null
---
Follow-up review of PR #51 by the user. Steps:
1. README labels: real doc titles, boldfaced, for the main docs; slugs for Architecture and Project Docs. Apply common-doc-guidelines; remove em dashes.
2. Essential Docs = Metaproc Concepts, Metaproc Design, Metaproc Operator Reference, Metaproc Developer Guide. The two shipped runbooks become an Operator Runbooks group.
3. Process Framework Theory moves into Reference Docs, presented as background.
4. Rename src/metaproc/docs/metaproc-concepts-and-principles.md -> metaproc-concepts.md; retitle 'Metaproc Concepts'.
5. Rename src/metaproc/docs/process-framework-concepts.md -> process-framework-theory.md; retitle 'Process Framework Theory'.
6. Sweep every reference: topic registry doc fields, HelpTopics default, check_distribution suffixes, sibling links in shipped docs, README, development.md, docs/project/, spec, code docstrings; regenerate the Agent Skill.
7. make verify; commit; push to PR #51; record the change in the spec Outcome.

## Notes

repren applied: 2 renames, 16 files rewritten. Next: H1 retitles, prose title sweep, README restructure with new grouping, pipe separators in Commands table.
