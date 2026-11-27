---
type: is
id: is-01m8f6x577q06angaxah10ye5g
title: "PR #131 C1: bound hashing to regular-file inputs"
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
created_at: 2026-11-27T19:14:53.286Z
updated_at: 2026-11-27T19:24:00.813Z
started_at: 2026-11-27T19:15:37.460Z
closed_at: 2026-11-27T19:24:00.813Z
close_reason: "Fixed in b828e9d7313cf31e1061ddc1d9827df843702363. Focused/adjacent regression suite: 197 passed; real Google GenAI SDK offline controls: 10 passed; full Python lint and type checks passed. Coordinator owns push, CI and disposition replies."
resolution: null
duplicate_of: null
---
Medium; src/metaproc/engine/dep_state.py:215. FIFO or nonregular input can block initial dispatch and completed-item resume. Review https://github.com/jlevy/metaproc/pull/131#pullrequestreview-6092373568. Check opened descriptor and avoid blocking FIFO opens; preserve absent and directory controls.
