---
type: is
id: is-01m8f80mwq9nwsd5czbtp8btv5
title: "PR #131 E1: textual HTTP refusal must veto earlier timeout"
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
created_at: 2026-11-27T19:34:16.214Z
updated_at: 2026-11-27T19:34:48.692Z
started_at: 2026-11-27T19:34:48.691Z
---
Medium finding E1 in https://github.com/jlevy/metaproc/pull/131#pullrequestreview-6092619120 at src/metaproc/engine/command_diagnostics.py:314 and :367. Explicit textual terminal HTTP 400/401/403 in an outer RuntimeError currently inherits retryability from an earlier timeout context or cause. Include narrowly parsed terminal 4xx statuses (except 408/429) in the handler-only precedence check; preserve valid transient, quota, and subprocess behavior. Add focused red/green regression coverage. Coordinator owns push, CI, disposition reply, and merge.
