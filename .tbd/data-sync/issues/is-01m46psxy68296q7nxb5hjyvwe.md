---
type: is
id: is-01m46psxy68296q7nxb5hjyvwe
title: Preserve Gemini workspace settings in auth checks and concurrent publication
kind: bug
status: closed
priority: 1
version: 3
delegate: codex@spud10.local
labels: []
dependencies: []
hold: null
hold_until: null
created_at: 2026-10-05T18:56:47.557Z
updated_at: 2026-10-05T19:08:00.574Z
started_at: 2026-10-05T18:57:00.554Z
closed_at: 2026-10-05T19:08:00.567Z
close_reason: Both regressions proven red before fix;180focusedtests,40concurrentpublicationchecks andfullmakeverify5292tests passed. PublicPR100 exacthead4d2dc2f7085d7bac4f1cdfe62fe8d997fdebf629 allfiveCIchecksgreen inrun37360605236. No merge.
resolution: null
duplicate_of: null
---
Workspace-scoped Gemini native settings must reach auth-check subprocesses through the adapter working directory. Settings publication must refuse a concurrent operator file instead of replacing it. Two regressions reproduce the unfixed behavior; use the existing atomic staging and owner-only write helpers.
