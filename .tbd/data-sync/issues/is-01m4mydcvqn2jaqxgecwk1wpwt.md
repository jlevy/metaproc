---
type: is
id: is-01m4mydcvqn2jaqxgecwk1wpwt
title: "PR #105 B4: isolation sentences overstate; ~/.gemini/.env still found by the .env walk"
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
created_at: 2026-10-11T07:39:07.508Z
updated_at: 2026-10-11T13:51:13.207Z
started_at: 2026-10-11T13:35:34.218Z
closed_at: 2026-10-11T13:51:13.206Z
close_reason: "Fixed in 1b0b32d (isolation sentences qualified: .env walk, sandbox file, home as cwd). Disposition: https://github.com/jlevy/metaproc/pull/105#issuecomment-6109722673"
resolution: null
duplicate_of: null
---
Low. Review B https://github.com/jlevy/metaproc/pull/105#pullrequestreview-5482362930. CHANGELOG.md:106, metaproc-operator-reference.md:800-802, credential-setup.runbook.md:354-356. findEnvFile walks cwd to / reading <dir>/.gemini/.env and <dir>/.env. Fix: qualify the three sentences and add a Migration note.
