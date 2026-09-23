---
type: is
id: is-01kyx4e9zcb1eyez4wkgc6qqkb
title: "PR #3 review R3: keep structure-report contract identity consistent"
kind: bug
status: closed
priority: 2
version: 3
labels:
  - pr-review
dependencies: []
parent_id: is-01kyx4dtrekwg385nrhzgvekdj
created_at: 2026-07-31T22:24:13.292Z
updated_at: 2026-07-31T22:49:27.104Z
closed_at: 2026-07-31T22:49:27.104Z
close_reason: "Resolved in fbee4d8; verified locally and in PR #3 CI, with disposition posted at issuecomment-5148108888."
---
PR #3 review R3 (Medium). References: CHANGELOG.md:26, src/metaproc/commands/softschema.py:142, src/metaproc/plugins/registry.py:109, src/metaproc/structure_report.py:78. Centralize the ID, enforce the payload identity, and correct the two-field migration guidance.

## Notes

Centralized STRUCTURE_REPORT_CONTRACT_ID, enforced the model/payload invariant, reused it in the writer and registry, and corrected both migration fields.
