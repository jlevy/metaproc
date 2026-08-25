---
type: is
id: is-01m0x4pfwfvjn46a595q2z3jsa
title: "R11: terminalize scalar attempts when cancellation or credential teardown fails"
kind: bug
status: closed
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0x358va0njc6k4g00pccj7e
created_at: 2026-08-25T19:00:05.134Z
updated_at: 2026-08-25T19:25:27.944Z
closed_at: 2026-08-25T19:25:27.943Z
close_reason: Fixed with primary-error preservation and terminal cancelled/lost attempt state under cancellation and teardown failure.
resolution: null
duplicate_of: null
---
A scalar agent attempt is marked running before launch, but the outer BaseException path only tears down its credential lease and re-raises. Cancellation or credential teardown failure can leave durable attempt/status state running forever. Preserve the original exception, best-effort release credentials, and terminalize an existing attempt as cancelled or lost; add focused cancellation and teardown-failure regressions.

## Notes

Fixed: scalar BaseException handling preserves the primary failure, best-effort tears down credentials, and terminalizes an existing attempt as cancelled or lost, including teardown failure.
