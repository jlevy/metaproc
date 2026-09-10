---
type: is
id: is-01m264kws89pvpk3jczjzfbcb0
title: "Model review follow-up: upgrade pinned clients for Fable 5.1"
kind: task
status: open
priority: 1
version: 1
labels: []
dependencies: []
parent_id: is-01m26342tjmj8qh22599bhd3sn
created_at: 2026-09-10T17:07:31.751Z
updated_at: 2026-09-10T17:07:31.751Z
---
The 2026-09-10 review confirms claude-fable-5-1 in Anthropic primary documentation, but Claude Code requires at least 2.1.257 while Metaproc pins 2.1.234. Pi 0.84.2 native catalog lacks the exact ID. Review a coordinated pinned-client and image upgrade under supply-chain policy, preserve version checks, verify model identity and tool calls on intended routes, and add Pi acceptance only after its deployment catalog supports the exact ID. PR 69 preserves the explicit Claude selection and documents this limitation; it does not establish live support.
