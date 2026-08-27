---
type: is
id: is-01m10z8yzxdnmg714n8extpy92
title: Add a gate for links that are valid in the repo and dead in the wheel
kind: feature
status: closed
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies:
  - type: blocks
    target: is-01m10z8z9aynagg69rfanemcnf
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-27T06:42:16.701Z
updated_at: 2026-08-27T15:07:48.726Z
closed_at: 2026-08-27T15:07:48.726Z
close_reason: Implemented in the documentation reorganization (phases 1-6).
resolution: null
duplicate_of: null
---
Phase 1. New devtools/check_shipped_links.py, wired into 'make lint-check'.

THE RULE: every relative link in src/metaproc/docs/*.md must resolve to a file inside src/metaproc/docs/. Anything else must be an absolute https://github.com/jlevy/metaproc/... URL.

Why a new gate: devtools/check_links.py resolves local links against the repository root, so a shipped doc linking ../../../docs/development.md passes CI while being dead for every reader of the installed wheel. That failure mode is invisible to every gate that exists today, and this plan multiplies the surface from 3 shipped docs to 15.

Measured today, before any move: the three shipped manuals carry 33 relative links, 26 of which escape src/metaproc/docs/. The rule is already violated - the gate just makes it visible.

Note the gate cannot check absolute GitHub URLs either. Prefer rewriting a link away over converting it to an absolute URL.
