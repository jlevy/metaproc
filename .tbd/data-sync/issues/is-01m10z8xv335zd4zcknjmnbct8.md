---
type: is
id: is-01m10z8xv335zd4zcknjmnbct8
title: Replace HelpTopics with a topic registry
kind: task
status: open
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies:
  - type: blocks
    target: is-01m10z8y4mxd1hc0hk68h0pmxh
  - type: blocks
    target: is-01m10z8ydpwg284vkt3p18d4kk
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-27T06:42:15.523Z
updated_at: 2026-08-27T06:43:40.639Z
---
Phase 1. src/metaproc/docs/__init__.py.

HelpTopics is a frozen dataclass with one field per topic, and skill/builtin.py enumerates topics with dataclasses.fields(HelpTopics). Two reasons that does not survive 15 topics:
- Field names must be Python identifiers, so a dashed topic like 'arch-file-io' cannot be a field name at all.
- Fifteen fields plus fifteen TOPIC_DESCRIPTIONS entries is two lists to keep in sync.

Replace with one registry entry per topic carrying: topic name, doc filename (no .md), one-line description, approximate word count. Derive TOPIC_DESCRIPTIONS from it and keep it exported with its current dict[str, str] type - AGENTS.md requires preserving public shapes, and it is imported by commands/help.py and skill/builtin.py.

Keep load_help_topics() working, and keep the lazy read: resource_doc_field defers the file read to construction, which matters more at 15 docs than at 3. Do not read all 15 to serve one.

Topic -> file mapping is the table in the spec's 'What ships' section.
