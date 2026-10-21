---
type: is
id: is-01m4myd32yzsb67gsznwrne19k
title: "PR #105 A5: runbook overstates that no launch reads the operator ~/.gemini (sandbox policy file)"
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
created_at: 2026-10-11T07:38:57.497Z
updated_at: 2026-10-11T13:51:11.293Z
started_at: 2026-10-11T13:35:33.598Z
closed_at: 2026-10-11T13:51:11.293Z
close_reason: "Fixed in 1b0b32d (runbook names sandbox policy file). Disposition: https://github.com/jlevy/metaproc/pull/105#issuecomment-6109722539"
resolution: null
duplicate_of: null
---
Low. Review A https://github.com/jlevy/metaproc/pull/105#pullrequestreview-5482267623. src/metaproc/docs/credential-setup.runbook.md:349-355. At 0.62.0 the sandbox policy file is resolved with os.homedir(). Fix: qualify the sentence.
