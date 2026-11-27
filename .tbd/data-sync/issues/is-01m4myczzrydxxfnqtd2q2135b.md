---
type: is
id: is-01m4myczzrydxxfnqtd2q2135b
title: "PR #105 A4: auth_info advertises personal OAuth the default scope cannot use"
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
created_at: 2026-10-11T07:38:54.324Z
updated_at: 2026-10-11T13:51:10.974Z
started_at: 2026-10-11T13:33:07.077Z
closed_at: 2026-10-11T13:51:10.973Z
close_reason: "Fixed in f0a8ac6 (auth_info lists Vertex ADC, explains login visibility). Disposition: https://github.com/jlevy/metaproc/pull/105#issuecomment-6109722539"
resolution: null
duplicate_of: null
---
Low. Review A https://github.com/jlevy/metaproc/pull/105#pullrequestreview-5482267623. adapters/gemini_cli.py:657-664 lists interactive login; under user scope a login in the operator ~/.gemini is invisible. Fix: replace with Vertex AI + ADC or qualify with workspace scope / template.
