---
type: is
id: is-01m0t5d3r59m3mpwg54j5s6qhf
title: "Review PR #34: pool scalar agent credentials"
kind: task
status: in_progress
priority: 1
version: 14
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
child_order_hints:
  - is-01m0t7zv0pz098m6rxm8d0sefg
  - is-01m0t7zvfvthf3bt667kdrwfgh
  - is-01m0t7zvyse3djcyn421pxn8mv
  - is-01m0t7zwe05cby0cp08dwf6z5q
  - is-01m0t7zwwvhe9a6mgykq8h2f5x
  - is-01m0t7zxcaed845hbkanka3ktz
  - is-01m0t7zxtshnt62pacpq2w0dkz
  - is-01m0t7zy9mrzsejcmpb2fhxdg4
  - is-01m0t7zyt479vkhz8fedxgesca
created_at: 2026-08-24T15:14:43.076Z
updated_at: 2026-08-24T22:32:05.522Z
---
Senior review of #34 (codex/gtia-v3-scalar-auth-policy). Addresses finding F4: scalar agent steps bypassing the credential pool. Verify: _execute_agent_step receives pool dispatch/auth flags via context, run_parallel duplication reduced not copied, tests assert pool-label usage. Post review comment; follow up before merge.

## Notes

ROUND 2 (2026-08-24, head 3d11a64): https://github.com/jlevy/metaproc/pull/34#issuecomment-5402358604 — Finding 1 FIXED WELL (scope_id = run_dir.relative_to(runs_dir); scalar+fanout share _bind_pool_dispatch; falsifiable tests). Findings 5/6/7/8/9/10 FIXED. Cloud-auth absorbed from closed #36 WITHOUT retry-later transport (verified) — #36 hazards absent. Finding 2 PARTIAL: warning is stderr-only, no event/log, worker leg + gcp-worker still silent. Finding 3 PARTIAL: only attempt 1; on retry >=2 status.yaml stays running (likely case — retry_exclude cools both labels). NEW: B1 containment CLIError raises inside un-guarded gather, can abort runs that previously completed; B2 scalar preflight NFS rglob per step with verdict discarded at warn posture, on the shared sync_executor. F1: worker leg does not enforce the invariant the docs now assert globally.
