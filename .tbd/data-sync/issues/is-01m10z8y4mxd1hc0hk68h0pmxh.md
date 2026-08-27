---
type: is
id: is-01m10z8y4mxd1hc0hk68h0pmxh
title: Show approximate topic sizes in metaproc help
kind: task
status: open
priority: 1
version: 1
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies: []
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-27T06:42:15.828Z
updated_at: 2026-08-27T06:42:15.828Z
---
Phase 1. src/metaproc/commands/help.py, the no-topic listing branch.

At 15 topics an agent picking one is picking how much context to spend: 'design' is 19,926 words, roughly 30k tokens. Print an approximate size per topic so that choice is informed rather than discovered after the dump.

Sizes come from the topic registry, not from reading the files at listing time - the listing must stay cheap.

Also widen the name column: 'arch-execution' is 14 chars against the current {name:<10}.
