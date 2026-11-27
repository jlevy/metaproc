---
type: is
id: is-01m8f6x5hmw7h71r4mk7m1k1nz
title: "PR #131 C2: omit rejected inputs from usage warnings"
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
created_at: 2026-11-27T19:14:53.619Z
updated_at: 2026-11-27T19:24:00.821Z
started_at: 2026-11-27T19:15:37.801Z
closed_at: 2026-11-27T19:24:00.821Z
close_reason: "Fixed in b828e9d7313cf31e1061ddc1d9827df843702363. Focused/adjacent regression suite: 197 passed; real Google GenAI SDK offline controls: 10 passed; full Python lint and type checks passed. Coordinator owns push, CI and disposition replies."
resolution: null
duplicate_of: null
---
Medium; src/metaproc/engine/resource_rollup.py:580. Raw validation exceptions repeat credential-bearing rejected inputs. Review https://github.com/jlevy/metaproc/pull/131#pullrequestreview-6092373568. Add safe malformed-event diagnostics regression.
