---
type: is
id: is-01m4mydg1seqr5fqgqaqsq0mbp
title: "PR #105 B6: template copied whole with no guard against a real home (pre-existing)"
kind: bug
status: closed
priority: 2
version: 3
delegate: claude-code@spud10
labels: []
dependencies: []
parent_id: is-01m4mybyqptgk7zm6660wmwtzb
hold: null
hold_until: null
created_at: 2026-10-11T07:39:10.723Z
updated_at: 2026-10-11T13:51:13.814Z
started_at: 2026-10-11T13:35:34.826Z
closed_at: 2026-10-11T13:51:13.813Z
close_reason: "Fixed in 1b0b32d (runbook: template is a prepared directory, never a real home). Disposition: https://github.com/jlevy/metaproc/pull/105#issuecomment-6109722673"
resolution: null
duplicate_of: null
---
Low suggestion, pre-existing. Review B https://github.com/jlevy/metaproc/pull/105#pullrequestreview-5482362930. adapters/gemini_cli.py:214-241. Fix (pick one): warn on credential-like files outside .gemini, or say in the runbook the template is a prepared directory, never a real home.
