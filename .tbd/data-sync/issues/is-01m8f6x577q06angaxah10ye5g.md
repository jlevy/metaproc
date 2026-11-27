---
type: is
id: is-01m8f6x577q06angaxah10ye5g
title: "PR #131 C1: bound hashing to regular-file inputs"
kind: bug
status: in_progress
priority: 2
version: 2
delegate: codex@spud10.local
labels: []
dependencies: []
parent_id: is-01m8f6wqr0j8zzppcjf16049fs
hold: null
hold_until: null
created_at: 2026-11-27T19:14:53.286Z
updated_at: 2026-11-27T19:15:37.461Z
started_at: 2026-11-27T19:15:37.460Z
---
Medium; src/metaproc/engine/dep_state.py:215. FIFO or nonregular input can block initial dispatch and completed-item resume. Review https://github.com/jlevy/metaproc/pull/131#pullrequestreview-6092373568. Check opened descriptor and avoid blocking FIFO opens; preserve absent and directory controls.
