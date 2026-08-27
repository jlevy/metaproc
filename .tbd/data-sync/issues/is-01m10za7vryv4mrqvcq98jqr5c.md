---
type: is
id: is-01m10za7vryv4mrqvcq98jqr5c
title: Extract Future Considerations from the design doc and the seven arch docs
kind: task
status: open
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies:
  - type: blocks
    target: is-01m10zav5m1qxns81taqs7m9d8
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-27T06:42:58.552Z
updated_at: 2026-08-27T06:43:42.695Z
---
Phase 4. Every one of the eight moved documents carries a Future Considerations section - the 'revise-architecture-doc' shortcut prompts for one on each revision.

In the design doc it is 'Open Questions' plus 'Potential Improvements', roughly 40 lines, including four '[unverified]' audit markers. Those markers are an auditor talking to a maintainer; they read as uncertainty about the product to anyone else.

Move each to docs/project/design/backlog/, one file per source document, each linked from docs/project/README.md. Also remove section 16 'Optional Workspace/State Surface (Future)' from the design doc to the same place - it is a future section that happens to sit in the body rather than at the end.

Shipped docs describe the system as it is. Backlog describes where it might go, and does not ship.
