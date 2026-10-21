---
type: is
id: is-01m4myd4gh8kxkgfy80cxt0kaz
title: "PR #105 A6: operator reference keeps unqualified shared Gemini registry contention paragraph"
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
created_at: 2026-10-11T07:38:58.960Z
updated_at: 2026-10-11T13:51:11.600Z
started_at: 2026-10-11T13:35:33.917Z
closed_at: 2026-10-11T13:51:11.599Z
close_reason: "Fixed in 1b0b32d (registry contention scoped to workspace scope). Disposition: https://github.com/jlevy/metaproc/pull/105#issuecomment-6109722539"
resolution: null
duplicate_of: null
---
Low. Review A https://github.com/jlevy/metaproc/pull/105#pullrequestreview-5482267623. src/metaproc/docs/metaproc-operator-reference.md:815-819 (METAPROC_HOST_MAX_LOCAL_AGENTS=1). With a private home per launch the registry is per launch; contention exists only under workspace scope. Fix: qualify; mention in CHANGELOG.
