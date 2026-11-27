---
type: is
id: is-01m8f6x4j12vp39s0jwn3dcnnb
title: "PR #131 B1: terminal HTTP status vetoes prior timeout"
kind: bug
status: closed
priority: 2
version: 3
delegate: codex@spud10.local
labels: []
dependencies: []
parent_id: is-01m8f6wqr0j8zzppcjf16049fs
hold: null
hold_until: null
created_at: 2026-11-27T19:14:52.608Z
updated_at: 2026-11-27T19:24:00.796Z
started_at: 2026-11-27T19:15:36.710Z
closed_at: 2026-11-27T19:24:00.796Z
close_reason: "Fixed in b828e9d7313cf31e1061ddc1d9827df843702363. Focused/adjacent regression suite: 197 passed; real Google GenAI SDK offline controls: 10 passed; full Python lint and type checks passed. Coordinator owns push, CI and disposition replies."
resolution: null
duplicate_of: null
---
Medium; src/metaproc/engine/command_diagnostics.py:302. A structured SDK 400/401/403 inherits transient cause or context. Review https://github.com/jlevy/metaproc/pull/131#pullrequestreview-6092311681. Preserve 408/429/5xx retries and hard-quota refusal.
