---
type: is
id: is-01m10c27wgdy7xzdkfw7wvf7v3
title: "PR #49 R1: Deduplicate resource evidence across equivalent path forms"
kind: bug
status: closed
priority: 1
version: 4
labels: []
dependencies: []
parent_id: is-01m10c27jjs2qh7hbcn3msz564
created_at: 2026-08-27T01:06:33.487Z
updated_at: 2026-08-27T01:54:10.870Z
closed_at: 2026-08-27T01:54:10.858Z
close_reason: null
resolution: null
duplicate_of: null
---
Normalize run and log paths before resource-event ownership and identity are derived. Finalization and recovery must count one provider invocation once even when equivalent relative and absolute source paths appear in different orders. Add exact meter, token, cost, and tool-count regressions.

## Notes

Fixed in the current PR49 worktree: canonicalize equivalent relative and absolute source paths before ownership and event identity. Finalization regression exercises both orderings and asserts one exact invocation, token totals, list cost, tool calls, and model turns. Full framework gate passes 4,476 tests with the same eight tracked skips; awaiting pushed commit, CI, and consumer rerun.
