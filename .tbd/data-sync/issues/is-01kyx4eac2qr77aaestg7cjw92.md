---
type: is
id: is-01kyx4eac2qr77aaestg7cjw92
title: "PR #3 review R4: repair public SoftSchema command examples"
kind: bug
status: closed
priority: 2
version: 3
labels:
  - pr-review
dependencies: []
parent_id: is-01kyx4dtrekwg385nrhzgvekdj
created_at: 2026-07-31T22:24:13.698Z
updated_at: 2026-07-31T22:49:27.112Z
closed_at: 2026-07-31T22:49:27.112Z
close_reason: "Resolved in fbee4d8; verified locally and in PR #3 CI, with disposition posted at issuecomment-5148108888."
---
PR #3 review R4 (Medium). References: docs/runbooks/softschema-validation.runbook.md:31-32 and README.md:125. Update contract IDs and required compile flags, then cover the examples with smoke or CLI tests.

## Notes

Corrected the runbook IDs/required --contract flag, fixed the README index, and added a compile CLI smoke test.
