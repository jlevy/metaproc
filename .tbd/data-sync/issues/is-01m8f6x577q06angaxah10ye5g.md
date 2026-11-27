---
type: is
id: is-01m8f6x577q06angaxah10ye5g
title: "PR #131 C1: bound hashing to regular-file inputs"
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
parent_id: is-01m8f6wqr0j8zzppcjf16049fs
created_at: 2026-11-27T19:14:53.286Z
updated_at: 2026-11-27T19:14:53.286Z
---
Medium; src/metaproc/engine/dep_state.py:215. FIFO or nonregular input can block initial dispatch and completed-item resume. Review https://github.com/jlevy/metaproc/pull/131#pullrequestreview-6092373568. Check opened descriptor and avoid blocking FIFO opens; preserve absent and directory controls.
