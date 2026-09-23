---
type: is
id: is-01kyx4e9jkqf1qy95aqt6f60bv
title: "PR #3 review R2: handle invalid contract IDs without tracebacks"
kind: bug
status: closed
priority: 2
version: 3
labels:
  - pr-review
dependencies: []
parent_id: is-01kyx4dtrekwg385nrhzgvekdj
created_at: 2026-07-31T22:24:12.882Z
updated_at: 2026-07-31T22:49:27.098Z
closed_at: 2026-07-31T22:49:27.098Z
close_reason: "Resolved in fbee4d8; verified locally and in PR #3 CI, with disposition posted at issuecomment-5148108888."
---
PR #3 review R2 (Medium). References: src/metaproc/commands/softschema.py:51,67. Convert invalid contract IDs at the CLI boundary into concise Metaproc validation errors and add CLI regressions for validate and compile.

## Notes

Added public SchemaMetadata prevalidation for --schema/--contract and subprocess regressions proving exit 2 without tracebacks.
