---
type: is
id: is-01m0vhr5rv34k6cbvr6wqx24sw
title: Consolidate mapped-scope runtime fixes on released main
kind: task
status: closed
priority: 0
version: 11
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - execution-model
dependencies:
  - type: blocks
    target: is-01m0vhs620ptcvxv074ccx88z4
parent_id: is-01m0r92q2y1pe7dmhrcj6nst7q
child_order_hints:
  - is-01m0vqngx1ergbsmwwcn9mtz8x
  - is-01m0x358va0njc6k4g00pccj7e
created_at: 2026-08-25T04:09:42.938Z
updated_at: 2026-08-25T19:37:17.769Z
closed_at: 2026-08-25T19:37:17.768Z
close_reason: "Clean draft PR #48 is pinned to 98cae8d1d42564454ad461612c40073b700e88b8; every review finding has a fixed, duplicate, or explicit evidence-triggered disposition; local make verify passed with 4,408 tests and 8 skips; all five exact-head GitHub CI jobs passed. No merge occurred, and downstream M0/scale gates remain open."
resolution: null
duplicate_of: null
---
Create one clean replacement branch from released main. Carry forward only the generic behavior required for in-process mapped scopes, shared execution context, scalar credential policy, cancellation safety, and one run-owned RunPool; exclude superseded retry transport, consumer evidence, and unrelated work. Preserve review domains in tests and public documentation. Open one draft pull request after local exact-head verification; do not merge.

## Notes

Clean consolidation is reviewed and locally verified from released post-PR-38 main. Historical over-design follow-ups are paused or evidence-triggered; superseded retry transport is excluded. Remaining: commit, push, open one draft PR, and wait for exact-head CI. Do not merge.
