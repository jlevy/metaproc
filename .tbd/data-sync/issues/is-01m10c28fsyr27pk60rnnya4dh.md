---
type: is
id: is-01m10c28fsyr27pk60rnnya4dh
title: "PR #49 R3: Resolve mapped-composite resource ownership to executable leaves"
kind: bug
status: closed
priority: 2
version: 4
labels: []
dependencies: []
parent_id: is-01m10c27jjs2qh7hbcn3msz564
created_at: 2026-08-27T01:06:34.104Z
updated_at: 2026-08-27T01:54:10.889Z
closed_at: 2026-08-27T01:54:10.889Z
close_reason: null
resolution: null
duplicate_of: null
---
Resource attribution for nested mapped composites must resolve to the executable leaf and item rather than stopping at a parent composite. Add a consumer-neutral ownership regression.

## Notes

Fixed in the current PR49 worktree with strict resource-snapshot/v2 mapped-composite IDs and a strict v1 reader. Source-free finalization now preserves executable leaf and item ownership for mapped process events and item/step-name collisions. Independent re-review found no Blocker or High; full framework gate passes 4,476 tests with eight tracked skips. Awaiting pushed commit, CI, and consumer rerun.
