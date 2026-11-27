---
type: is
id: is-01m8f6x47c3nzcfjp8jg15wke0
title: "PR #131 A1: require positive transient handler evidence"
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
created_at: 2026-11-27T19:14:52.267Z
updated_at: 2026-11-27T19:24:00.785Z
started_at: 2026-11-27T19:15:36.429Z
closed_at: 2026-11-27T19:24:00.784Z
close_reason: "Fixed in b828e9d7313cf31e1061ddc1d9827df843702363. Focused/adjacent regression suite: 197 passed; real Google GenAI SDK offline controls: 10 passed; full Python lint and type checks passed. Coordinator owns push, CI and disposition replies."
resolution: null
duplicate_of: null
---
Medium; src/metaproc/engine/command_diagnostics.py:315. Bare numbers, filenames, and field names currently trigger broad subprocess text patterns. Review https://github.com/jlevy/metaproc/pull/131#pullrequestreview-6092241638. Add classification and one-attempt fan-out regressions.
