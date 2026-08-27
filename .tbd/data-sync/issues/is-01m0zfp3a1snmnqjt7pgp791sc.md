---
type: is
id: is-01m0zfp3a1snmnqjt7pgp791sc
title: tbd review-github-pr shortcut points at a docs/project/reviews/ directory this repo retired
kind: chore
status: closed
priority: 3
version: 2
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies: []
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-26T16:50:35.457Z
updated_at: 2026-08-27T17:38:40.908Z
closed_at: 2026-08-27T17:38:40.907Z
close_reason: "Noted in AGENTS.md, placed outside the generated TBD INTEGRATION block so tbd setup cannot overwrite it: reviews go on the PR, and docs/project/reviews/ is not to be recreated."
resolution: null
duplicate_of: null
---
The shortcut offers 'In-repo review doc: docs/project/reviews/review-YYYY-MM-DD-topic.md' as a publish channel, but docs/reviews/ was deliberately deleted in 8aacb14 and b266f5c as 'human-facing workflow scaffolding'. An agent following the shortcut would recreate a retired directory. Either note the exclusion in AGENTS.md or raise it upstream against the tbd shortcut.
