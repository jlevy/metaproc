---
type: is
id: is-01m8f6x47c3nzcfjp8jg15wke0
title: "PR #131 A1: require positive transient handler evidence"
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
parent_id: is-01m8f6wqr0j8zzppcjf16049fs
created_at: 2026-11-27T19:14:52.267Z
updated_at: 2026-11-27T19:14:52.267Z
---
Medium; src/metaproc/engine/command_diagnostics.py:315. Bare numbers, filenames, and field names currently trigger broad subprocess text patterns. Review https://github.com/jlevy/metaproc/pull/131#pullrequestreview-6092241638. Add classification and one-attempt fan-out regressions.
