---
type: is
id: is-01m1d4p503de0qpgpq8tvc93v7
title: Land the plan-backed fan-out status total through the release gate
kind: task
status: open
priority: 1
version: 1
labels:
  - status
  - release-blocker
dependencies: []
parent_id: is-01m1d3zgc5kwnxvarym7ebgsyk
created_at: 2026-09-01T00:07:44.898Z
updated_at: 2026-09-01T00:07:44.898Z
---
A two-commit branch fixes run-process fan-out totals by reading per-step item keys from the recorded run plan, and its focused status suite passes. The downstream V3 candidate pins it, but it has no Metaproc pull request, is not on main, and its exact commit fails the public-hygiene portion of make verify because of downstream-specific commit text. Recreate or reword a public-safe branch, open the normal review boundary, run the complete exact-head gate and CI, merge it, and repin the consumer to the merged commit.
