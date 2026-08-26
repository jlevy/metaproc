---
type: is
id: is-01m0xv4aaa19xn4tmw8v68f401
title: "PR #48: eliminate post-#44 GCP entrypoint auth test order dependency"
kind: bug
status: closed
priority: 0
version: 5
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels: []
dependencies:
  - type: blocks
    target: is-01m0xrg6jeywxa1hwns3eay01m
parent_id: is-01m0xrg4vr6n4znzxz0kkxxxt7
created_at: 2026-08-26T01:32:06.848Z
updated_at: 2026-08-26T01:35:21.556Z
closed_at: 2026-08-26T01:35:21.555Z
close_reason: The order dependency is removed and both focused and combined suites are green.
resolution: null
duplicate_of: null
---
After rebasing PR #48 onto merged PR #44, the combined GCP/auth overlap suite fails because the CODEX_CREDS_JSON entrypoint bootstrap test does not materialize auth.json when run after neighboring tests. Identify the leaked state or production defect, fix it hermetically, add a regression if needed, and make this block final readiness.

## Notes

Fixed after rebasing onto merged PR #44: the worker bootstrap guard now accepts an injected environment mapping, so its unit tests no longer leak the one-shot METAPROC_AUTH_POOL_RUN mutation. Direct entrypoint pair: 47 passed. Combined GCP/auth overlap suite: 316 passed, 4 skipped. Recorded as R15 in the public plan and committed in f94b8a9.
