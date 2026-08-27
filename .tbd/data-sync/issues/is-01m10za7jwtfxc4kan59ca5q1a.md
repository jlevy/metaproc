---
type: is
id: is-01m10za7jwtfxc4kan59ca5q1a
title: Extract the design doc Revision History to docs/project
kind: task
status: closed
priority: 1
version: 4
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies:
  - type: blocks
    target: is-01m10zav5m1qxns81taqs7m9d8
  - type: blocks
    target: is-01m10za8d67et41efm9qre9mkm
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-27T06:42:58.268Z
updated_at: 2026-08-27T15:07:50.579Z
closed_at: 2026-08-27T15:07:50.579Z
close_reason: Implemented in the documentation reorganization (phases 1-6).
resolution: null
duplicate_of: null
---
Phase 4. rev2e through rev2o, currently the tail of the doc from the 'Revision History' heading to EOF.

Move verbatim to docs/project/design/metaproc-design-revisions.md, linked from docs/project/README.md. Also delete the 'Revision: rev2m' header line near the top of the doc.

These are authoring revisions, not releases. They mean nothing to someone reading the doc through 'metaproc help design' from an installed wheel, and they are exactly the project-internal provenance that docs/project/README.md says belongs under docs/project/.
