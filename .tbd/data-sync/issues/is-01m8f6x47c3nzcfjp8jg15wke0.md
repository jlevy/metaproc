---
type: is
id: is-01m8f6x47c3nzcfjp8jg15wke0
title: "PR #131 A1: require positive transient handler evidence"
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
created_at: 2026-11-27T19:14:52.267Z
updated_at: 2026-11-27T19:15:36.430Z
started_at: 2026-11-27T19:15:36.429Z
---
Medium; src/metaproc/engine/command_diagnostics.py:315. Bare numbers, filenames, and field names currently trigger broad subprocess text patterns. Review https://github.com/jlevy/metaproc/pull/131#pullrequestreview-6092241638. Add classification and one-attempt fan-out regressions.
