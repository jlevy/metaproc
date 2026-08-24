---
type: is
id: is-01m0t8068yxh3je3xnfrq8f70k
title: "PR #36 review 3A: validate retry options without auth account"
kind: bug
status: open
priority: 2
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d4f3vpf2e815ryxxqkp7
created_at: 2026-08-24T16:00:05.404Z
updated_at: 2026-08-24T16:00:05.404Z
---
Review https://github.com/jlevy/metaproc/pull/36#issuecomment-5397585537. Both CLIs validate retry-later values only when --auth-account is set, so invalid explicit input silently succeeds. Parse and validate all explicitly provided options at the command boundary and raise CLIError.
