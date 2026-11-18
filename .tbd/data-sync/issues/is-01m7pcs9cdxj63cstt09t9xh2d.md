---
type: is
id: is-01m7pcs9cdxj63cstt09t9xh2d
title: Scrub CODEX_ACCESS_TOKEN from pooled Codex slot launches
kind: task
status: open
priority: 2
version: 1
spec_path: docs/project/specs/active/plan-2026-09-10-model-catalog-followups.md
labels: []
dependencies: []
parent_id: is-01m7p3pwxkys2pdd65g2kagg7b
created_at: 2026-11-18T03:56:37.133Z
updated_at: 2026-11-18T03:56:37.133Z
---
Codex 0.166.0 ranks CODEX_ACCESS_TOKEN above a slot's stored auth.json, so an ambient token in the operator environment could make a pooled ChatGPT-login slot run on another credential. The November refresh added CODEX_API_KEY to credential_scrub_env but left CODEX_ACCESS_TOKEN, because scrubbing it changes pool behavior. Decide and add it with a test, mirroring the Claude profile/federation scrub.
