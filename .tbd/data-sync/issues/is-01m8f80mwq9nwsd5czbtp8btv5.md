---
type: is
id: is-01m8f80mwq9nwsd5czbtp8btv5
title: "PR #131 E1: textual HTTP refusal must veto earlier timeout"
kind: bug
status: closed
priority: 2
version: 3
delegate: codex@spud10.local
labels: []
dependencies: []
parent_id: is-01m8f6wqr0j8zzppcjf16049fs
hold: null
hold_until: null
created_at: 2026-11-27T19:34:16.214Z
updated_at: 2026-11-27T19:37:52.347Z
started_at: 2026-11-27T19:34:48.691Z
closed_at: 2026-11-27T19:37:52.346Z
close_reason: "Fixed E1 locally in d37734a5ac6dbe3a19ebf7a77ec1607e783c5e8c. Explicit terminal textual HTTP statuses use the existing handler-only regex and veto older transient causes/contexts; 408/429 remain exempt. Red: 6 new textual 400/401/403 cases failed while 6 structured controls passed. Green: 170 focused handler/command-diagnostic/generic-retry tests passed; ruff format/check, codespell and basedpyright passed. Coordinator retains push, CI, disposition reply and merge."
resolution: null
duplicate_of: null
---
Medium finding E1 in https://github.com/jlevy/metaproc/pull/131#pullrequestreview-6092619120 at src/metaproc/engine/command_diagnostics.py:314 and :367. Explicit textual terminal HTTP 400/401/403 in an outer RuntimeError currently inherits retryability from an earlier timeout context or cause. Include narrowly parsed terminal 4xx statuses (except 408/429) in the handler-only precedence check; preserve valid transient, quota, and subprocess behavior. Add focused red/green regression coverage. Coordinator owns push, CI, disposition reply, and merge.
