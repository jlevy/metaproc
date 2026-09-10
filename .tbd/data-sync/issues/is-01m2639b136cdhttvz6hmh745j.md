---
type: is
id: is-01m2639b136cdhttvz6hmh745j
title: "PR 69: verify Anthropic and Claude Code model support"
kind: task
status: closed
priority: 1
version: 5
spec_path: docs/project/specs/active/plan-2026-09-10-model-catalog-followups.md
labels: []
dependencies: []
parent_id: is-01m26342tjmj8qh22599bhd3sn
created_at: 2026-09-10T16:44:17.314Z
updated_at: 2026-09-10T18:45:33.322Z
closed_at: 2026-09-10T17:42:10.442Z
close_reason: Official source and pinned-client review complete; catalog fixes integrated and remaining client/migration/live-evidence work tracked in separate sub-beads.
resolution: null
duplicate_of: null
---
Sol research: verify canonical Anthropic IDs, Claude Code aliases and minimum client versions, model lifecycle and effort limits from official documentation.

## Notes

Sol research and root primary-source checks confirm original new Claude IDs, plus Fable 5 and Opus 4.8. Pinned Pi 0.84.2 source confirms four additional full Anthropic IDs but lacks Fable 5.1. Claude Code Fable 5.1 minimum 2.1.257 exceeds repository pin 2.1.234; limitation is documented and follow-up mp-xgrd owns upgrade and live verification.
