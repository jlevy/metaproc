---
type: is
id: is-01kyx4e95revxhwb2madja8n26
title: "PR #3 review R1: document the complete downstream breaking surface"
kind: bug
status: closed
priority: 1
version: 4
labels:
  - pr-review
dependencies: []
parent_id: is-01kyx4dtrekwg385nrhzgvekdj
created_at: 2026-07-31T22:24:12.472Z
updated_at: 2026-07-31T22:49:27.085Z
closed_at: 2026-07-31T22:49:27.085Z
close_reason: "Resolved in fbee4d8; verified locally and in PR #3 CI, with disposition posted at issuecomment-5148108888."
---
PR #3 review R1 (High). References: pyproject.toml:30 and CHANGELOG.md:14-21. Audit SoftSchema 0.2/0.3 breaking behavior exposed by Metaproc, document general contract-ID migration plus portable YAML and offline-reference changes, and add focused compatibility coverage.

## Notes

Expanded release notes across contract IDs, portable YAML, offline refs, and linked upstream 0.2/0.3 notes; added portable-input and outcome compatibility coverage.
