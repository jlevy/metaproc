---
type: is
id: is-01m0zfmnmjz12f0evrmddyh8az
title: Reframe metaproc-design.md as a shipped design document
kind: task
status: closed
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies: []
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-26T16:49:48.689Z
updated_at: 2026-08-27T15:07:50.306Z
closed_at: 2026-08-27T15:07:50.305Z
close_reason: Implemented in the documentation reorganization (phases 1-6).
resolution: null
duplicate_of: null
---
Phase 4. NOTE: this reverses the earlier instruction on this bead, which said to keep the Revision History. The doc now ships in the wheel, so authoring revisions must come out (see the extraction bead).

Front matter: title 'Metaproc Design'; description and status reflecting a design record that ships as the 'design' help topic. It currently reads 'Architecture: Metaproc Core' with status Approved.

Keep: the section numbering and the note explaining why numbering starts at section 5 (its first four sections became the concepts doc). That note is the evidence for the rename.

Remove to docs/project/: the 'Revision: rev2m' header line (line ~24), the Revision History section, and the Future Considerations section. Separate beads cover each.

The body H1 at line 7 must be retitled too - fmf_read strips front matter before 'metaproc help' serves the body, so the H1 is the only title a CLI reader sees.
