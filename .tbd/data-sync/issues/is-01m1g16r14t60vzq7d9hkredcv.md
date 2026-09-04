---
type: is
id: is-01m1g16r14t60vzq7d9hkredcv
title: "Gemini adapter: honor or reject no_session_persistence"
kind: task
status: open
priority: 2
version: 1
spec_path: docs/project/specs/active/plan-2026-09-01-safeproc-local-incubation.md
labels: []
dependencies: []
parent_id: is-01m1fxnwnyqvq1gg8ak7317kyc
created_at: 2026-09-02T03:04:37.668Z
updated_at: 2026-09-02T03:04:37.668Z
---
gemini.py lists no_session_persistence in its allowed keys (line 119) but never consumes it, while claude_code.py:560 does. Either give it a tested Gemini-native contract (disable general.sessionRetention.enabled or select isolated bounded project state) or reject the key with a clear error. Independent of Safeproc. Origin: agent CLI research record and review F7 of pull request 62.
