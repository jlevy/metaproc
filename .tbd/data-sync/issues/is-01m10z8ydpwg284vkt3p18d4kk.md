---
type: is
id: is-01m10z8ydpwg284vkt3p18d4kk
title: Enumerate skill topics from the registry and regenerate the skill
kind: task
status: closed
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies: []
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-27T06:42:16.117Z
updated_at: 2026-08-27T15:07:48.187Z
closed_at: 2026-08-27T15:07:48.186Z
close_reason: Implemented in the documentation reorganization (phases 1-6).
resolution: null
duplicate_of: null
---
Phase 1. src/metaproc/skill/builtin.py.

_help_topic_catalog() calls dataclasses.fields(HelpTopics); switch it to the registry. It sorts topic names alphabetically, which will interleave the arch-* topics with the core ones - decide whether to group instead, since the generated catalog is what an agent reads first.

After the change run 'metaproc skill metaproc --install' to regenerate .agents/skills/metaproc/ and .claude/skills/metaproc/. A drift test enforces the committed copies match, so this is not optional and it will fail CI if skipped.
