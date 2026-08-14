---
type: is
id: is-01kyjmbrs0r2gf47zfdb1rr08c
title: Remove human-facing repository workflow scaffolding
kind: task
status: closed
priority: 2
version: 6
spec_path: docs/project/specs/done/plan-2026-07-26-standalone-extraction.md
labels: []
dependencies: []
parent_id: is-01kygat035xcheze599f3yxqrb
created_at: 2026-07-27T20:30:48.604Z
updated_at: 2026-08-09T18:57:23.188Z
closed_at: 2026-07-27T20:43:46.743Z
close_reason: "Agent-facing workflow cleanup is committed, pushed, documented, and fully verified on PR #1."
---
Remove GitHub PR and issue forms plus generic contributor/community workflow documents. Keep agent instructions, technical development guidance, runbooks, architecture docs, executable process specifications, CI, and release automation as the repository workflow contract.

## Notes

Removed GitHub pull-request and issue forms, generic contributor/community documents, and the empty design-review placeholder. Preserved agent instructions, development guidance, runbooks, executable process specifications, CI, and publishing automation. Commit b266f5c95d3919be1672fb74046d3c24d8c33bda passes make verify (3,784 passed, 8 skipped), strict private-reference scanning, and hosted lint, distribution, and Python 3.12/3.13/3.14 checks.
