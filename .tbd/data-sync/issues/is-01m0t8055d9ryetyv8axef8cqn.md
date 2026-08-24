---
type: is
id: is-01m0t8055d9ryetyv8axef8cqn
title: "PR #36 review 2: state retry-later transport is inert"
kind: bug
status: open
priority: 2
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d4f3vpf2e815ryxxqkp7
created_at: 2026-08-24T16:00:04.266Z
updated_at: 2026-08-24T16:00:04.266Z
---
Review https://github.com/jlevy/metaproc/pull/36#issuecomment-5397585537. Architecture/docstrings claim the policy is consulted although no caller invokes wait_for_pool_recovery, and preflight advertises a no-op remedy; CHANGELOG is missing. Correct the docs or remove the unused surface until a consumer lands.
