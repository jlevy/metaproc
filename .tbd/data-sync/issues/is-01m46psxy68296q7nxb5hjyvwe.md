---
type: is
id: is-01m46psxy68296q7nxb5hjyvwe
title: Preserve Gemini workspace settings in auth checks and concurrent publication
kind: bug
status: in_progress
priority: 1
version: 2
delegate: codex@spud10.local
labels: []
dependencies: []
hold: null
hold_until: null
created_at: 2026-10-05T18:56:47.557Z
updated_at: 2026-10-05T18:57:00.555Z
started_at: 2026-10-05T18:57:00.554Z
---
Workspace-scoped Gemini native settings must reach auth-check subprocesses through the adapter working directory. Settings publication must refuse a concurrent operator file instead of replacing it. Two regressions reproduce the unfixed behavior; use the existing atomic staging and owner-only write helpers.
