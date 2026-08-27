---
type: is
id: is-01m10zav5m1qxns81taqs7m9d8
title: Re-measure topic sizes after the tightening passes
kind: chore
status: open
priority: 2
version: 1
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies: []
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-27T06:43:18.324Z
updated_at: 2026-08-27T06:43:18.324Z
---
Phase 5, last step. The word counts in the topic registry are measured against the pre-tightening documents. Phases 4 and 5 remove the revision histories, the backlogs, the maintenance blockquotes, and whatever the cohesion review turns up.

Re-measure all fifteen and update the registry, so 'metaproc help' does not overstate what it is about to print. Consider a test that recomputes them and fails on drift beyond a tolerance, rather than a comment asking the next person to remember.
