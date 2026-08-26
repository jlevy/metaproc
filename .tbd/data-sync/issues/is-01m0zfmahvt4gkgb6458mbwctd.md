---
type: is
id: is-01m0zfmahvt4gkgb6458mbwctd
title: Move architecture and design docs under docs/project
kind: task
status: open
priority: 1
version: 4
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies:
  - type: blocks
    target: is-01m0zfmnmjz12f0evrmddyh8az
  - type: blocks
    target: is-01m0zfmp3h1fefdr5bc88zp9c8
  - type: blocks
    target: is-01m0zfmpjey3fp75kgm27agrbz
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-26T16:49:37.339Z
updated_at: 2026-08-26T16:49:49.645Z
---
Create docs/project/design/ and docs/project/arch/. git mv arch-metaproc-core.md to docs/project/design/metaproc-design.md (97 refs across 52 files), move the other seven arch-*.md into docs/project/arch/, and move execution-model-design.md and process-framework-concepts.md into docs/project/design/. Sweep all inbound links including Python docstrings in src/metaproc/execution_model/. Verify with devtools.check_links.
