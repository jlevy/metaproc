---
type: is
id: is-01kyjjswdamtc9d94pwbfqn79r
title: Enable downstream CI access to the standalone repository
kind: task
status: blocked
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-07-26-standalone-extraction.md
labels: []
dependencies: []
parent_id: is-01kygat035xcheze599f3yxqrb
created_at: 2026-07-27T20:03:33.925Z
updated_at: 2026-07-27T20:03:38.252Z
---
Hosted downstream checks cannot clone the cross-owner submodule while this repository is private. Resolve by explicitly approving public visibility or provisioning least-privilege cross-repository read access; do not weaken checkout or credential handling.

## Notes

Awaiting an explicit repository-access decision because changing visibility or provisioning credentials is an external security action. The standalone PR's own CI is unaffected.
