---
type: is
id: is-01kyjjswdamtc9d94pwbfqn79r
title: Enable downstream CI access to the standalone repository
kind: task
status: closed
priority: 1
version: 5
spec_path: docs/project/specs/done/plan-2026-07-26-standalone-extraction.md
labels: []
dependencies: []
parent_id: is-01kygat035xcheze599f3yxqrb
created_at: 2026-07-27T20:03:33.925Z
updated_at: 2026-08-09T18:57:08.141Z
closed_at: 2026-08-09T18:55:55.413Z
close_reason: github.com/jlevy/metaproc is public, so hosted downstream jobs no longer require cross-repository credentials to clone the pinned submodule.
---
Hosted downstream checks cannot clone the cross-owner submodule while this repository is private. Resolve by explicitly approving public visibility or provisioning least-privilege cross-repository read access; do not weaken checkout or credential handling.

## Notes

Awaiting an explicit repository-access decision because changing visibility or provisioning credentials is an external security action. The standalone PR's own CI is unaffected.
