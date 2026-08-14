---
type: is
id: is-01kzkx36g1636w0njkr0bzw4er
title: "PR #2 review R3: enforce both committed Agent Skill copies"
kind: bug
status: closed
priority: 1
version: 2
labels: []
dependencies: []
parent_id: is-01kzkwt9ddwj9sfvjwzt7ma027
created_at: 2026-08-09T18:38:21.184Z
updated_at: 2026-08-09T18:51:39.437Z
closed_at: 2026-08-09T18:51:39.437Z
close_reason: Resolved in 83b894d; focused contracts, make verify, pre-push verification, and all fresh GitHub checks pass.
---
Fix the committed-skill drift test so a missing .agents or .claude copy fails in a source checkout instead of skipping.
