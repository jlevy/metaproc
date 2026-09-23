---
type: is
id: is-01m10z8ypvc86kvrgc42sd8xpy
title: Extend check_distribution.py with the twelve new documents
kind: task
status: closed
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies: []
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-27T06:42:16.411Z
updated_at: 2026-08-27T15:07:48.458Z
closed_at: 2026-08-27T15:07:48.458Z
close_reason: Implemented in the documentation reorganization (phases 1-6).
resolution: null
duplicate_of: null
---
Phase 1. devtools/check_distribution.py.

Both _inspect_wheel (required_suffixes, 'metaproc/docs/...') and _inspect_sdist (required_suffixes, 'src/metaproc/docs/...') currently assert only metaproc-operator-reference.md. Add all twelve new filenames to both sets.

Assert the new payload rather than tolerate it: this plan deliberately changes the distribution from 13,742 to 75,844 words, roughly 470 KB of Markdown. A check that only spot-checks one file would not notice a doc silently dropping out of the wheel, which is the exact failure this plan makes possible.

packages = ["src/metaproc"] in pyproject.toml already includes .md under the package tree, so no force-include entry is needed.
