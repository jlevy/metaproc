---
type: is
id: is-01m0t8068yxh3je3xnfrq8f70k
title: "PR #36 review 3A: validate retry options without auth account"
kind: bug
status: closed
priority: 2
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d4f3vpf2e815ryxxqkp7
created_at: 2026-08-24T16:00:05.404Z
updated_at: 2026-08-24T19:04:20.701Z
closed_at: 2026-08-24T19:04:20.701Z
close_reason: "Fixed by deleting the unconsumed retry-later flags, defaults, typed/raw dual representation, and duplicate parser from PR #36. Documentation and preflight text were corrected to stop advertising inert behavior. No compatibility-skew transport remains."
resolution: null
duplicate_of: null
---
Review https://github.com/jlevy/metaproc/pull/36#issuecomment-5397585537. Both CLIs validate retry-later values only when --auth-account is set, so invalid explicit input silently succeeds. Parse and validate all explicitly provided options at the command boundary and raise CLIError.
