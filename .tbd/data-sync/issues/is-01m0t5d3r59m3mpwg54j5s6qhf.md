---
type: is
id: is-01m0t5d3r59m3mpwg54j5s6qhf
title: "Review PR #34: pool scalar agent credentials"
kind: task
status: in_progress
priority: 1
version: 3
labels:
  - pr-review
dependencies: []
created_at: 2026-08-24T15:14:43.076Z
updated_at: 2026-08-24T15:38:48.988Z
---
Senior review of #34 (codex/gtia-v3-scalar-auth-policy). Addresses finding F4: scalar agent steps bypassing the credential pool. Verify: _execute_agent_step receives pool dispatch/auth flags via context, run_parallel duplication reduced not copied, tests assert pool-label usage. Post review comment; follow up before merge.

## Notes

Review posted 2026-08-24: https://github.com/jlevy/metaproc/pull/34#issuecomment-5397585053 — verdict: changes requested. Must-fix: (1) HIGH run_id rebind (run_process.py:2507, :1581) uses logical spec-name/run-context id, relocating credential slot dirs outside the run tree incl. production fan-out path — bind path-relative id + slot-dir-under-run_dir test; (2) silent adapter-mismatch ambient fallback needs warning/auth_skipped event; (3) top-level scalar pool exhaustion aborts run with step stuck 'running'. Also: completion-side lease-leak window on cancel (confirm #35 covers), PoolAuthOverrideError unwrapped, scalar bypasses quota preflight, ambient-scrub test asserts parent not child. FOLLOW UP: verify fixes land before merge.
