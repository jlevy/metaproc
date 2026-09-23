---
type: is
id: is-01m10zavekbtk3qf7944msf7xr
title: Note in AGENTS.md that core docs ship in the wheel
kind: task
status: closed
priority: 2
version: 2
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies: []
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-27T06:43:18.611Z
updated_at: 2026-08-27T15:07:50.036Z
closed_at: 2026-08-27T15:07:50.036Z
close_reason: Implemented in the documentation reorganization (phases 1-6).
resolution: null
duplicate_of: null
---
Phase 2. AGENTS.md currently says: 'After changing the skill baseline, spec, or help topics, regenerate the committed copies with metaproc skill metaproc --install (a drift test enforces this).'

That stays true, but the surrounding guidance assumes the shipped set is three manuals. After this plan, editing any of fifteen documents changes the wheel, and the shipped-link rule constrains what those documents may link to.

Add both facts to the Metaproc Self-Documentation section: which documents ship, and that a relative link in src/metaproc/docs/ may not escape that directory.
