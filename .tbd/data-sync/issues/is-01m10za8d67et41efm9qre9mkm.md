---
type: is
id: is-01m10za8d67et41efm9qre9mkm
title: Replace revision markers with release versions in shipped prose
kind: task
status: open
priority: 1
version: 1
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies: []
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-27T06:42:59.110Z
updated_at: 2026-08-27T06:42:59.110Z
---
Phase 4. Where a shipped document dates a statement, it should name a release, not an authoring revision.

Mapping, from the git tags:
- rev2i and earlier (<= 2026-04-20): before v0.2.0
- rev2j, rev2k (2026-08-02/03): v0.2.1 (2026-08-09)
- rev2l (2026-08-09): v0.2.1
- rev2m, rev2n (2026-08-24): v0.3.0 (2026-08-24)
- rev2o (2026-08-25): unreleased at time of writing

This is not just deletion. A sentence that says a behavior arrived in rev2n should say it arrived in v0.3.0 - that is a fact a reader of the package can act on, and the revision number never was.
