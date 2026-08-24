---
type: is
id: is-01m0t5d3r59m3mpwg54j5s6qhf
title: "Review PR #34: pool scalar agent credentials"
kind: task
status: in_progress
priority: 1
version: 13
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
updated_at: 2026-08-24T15:59:57.764Z
---
Senior review of #34 (codex/gtia-v3-scalar-auth-policy). Addresses finding F4: scalar agent steps bypassing the credential pool. Verify: _execute_agent_step receives pool dispatch/auth flags via context, run_parallel duplication reduced not copied, tests assert pool-label usage. Post review comment; follow up before merge.

## Notes

Review posted 2026-08-24: https://github.com/jlevy/metaproc/pull/34#issuecomment-5397585053 — verdict: changes requested. Must-fix: (1) HIGH run_id rebind (run_process.py:2507, :1581) uses logical spec-name/run-context id, relocating credential slot dirs outside the run tree incl. production fan-out path — bind path-relative id + slot-dir-under-run_dir test; (2) silent adapter-mismatch ambient fallback needs warning/auth_skipped event; (3) top-level scalar pool exhaustion aborts run with step stuck 'running'. Also: completion-side lease-leak window on cancel (confirm #35 covers), PoolAuthOverrideError unwrapped, scalar bypasses quota preflight, ambient-scrub test asserts parent not child. FOLLOW UP: verify fixes land before merge.
