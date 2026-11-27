---
type: is
id: is-01m8f6x4wq9rt48qmc41te2qk3
title: "PR #131 B2: rerun malformed input digest results"
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
created_at: 2026-11-27T19:14:52.951Z
updated_at: 2026-11-27T19:15:37.055Z
started_at: 2026-11-27T19:15:37.054Z
---
Medium; src/metaproc/commands/run_process.py:3103. Malformed new-format results silently permit key-based reuse. Review https://github.com/jlevy/metaproc/pull/131#pullrequestreview-6092311681. Keep valid legacy compatibility and add resume regression.
