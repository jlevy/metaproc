---
type: is
id: is-01m4mydecbryqtt127v4q6m3z1
title: "PR #105 B5: docs understate what the private home retains (transcripts, tokens) and after a kill"
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
created_at: 2026-10-11T07:39:09.064Z
updated_at: 2026-10-11T13:51:13.512Z
started_at: 2026-10-11T13:35:34.517Z
closed_at: 2026-10-11T13:51:13.511Z
close_reason: "Fixed in 1b0b32d (runbook names retained state, private TMPDIR, cleanup after kill). Disposition: https://github.com/jlevy/metaproc/pull/105#issuecomment-6109722673"
resolution: null
duplicate_of: null
---
Low. Review B https://github.com/jlevy/metaproc/pull/105#pullrequestreview-5482362930. credential-setup.runbook.md:392-394 and arch-runpool.md:293-294. Fix: name transcripts and refreshed tokens, require a private TMPDIR, and say to remove metaproc-gemini-* left by a killed process; or adopt A3 reclaim.
