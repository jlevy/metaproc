---
type: is
id: is-01m0zfmayr72s63rwxm2724mt1
title: Rename the rev3 proposals doc and keep it out of the wheel
kind: task
status: open
priority: 2
version: 2
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies: []
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-26T16:49:37.752Z
updated_at: 2026-08-27T06:41:46.248Z
---
Phase 4. git mv docs/metaproc-design-rev3-proposals.md -> docs/project/design/metaproc-design-proposals.md and drop 'rev3' from the title and prose; a specifically numbered next revision is not committed to. 14 refs across 4 files.

New constraint: this document is entirely future-work backlog, so it must NOT ship and no shipped document may link to it. The design doc links to it twice (its header 'Additional reference docs' list, and the line under Future Considerations). Both go - see the backlog-extraction bead.
