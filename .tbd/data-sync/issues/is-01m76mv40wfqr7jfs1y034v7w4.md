---
type: is
id: is-01m76mv40wfqr7jfs1y034v7w4
title: "PR #121 A6: never in the child of a mapped composite does not re-run for completed items"
kind: bug
status: closed
priority: 2
version: 3
delegate: claude-code@spud10.local
labels: []
dependencies: []
parent_id: is-01m76msm7xe6a3n7dcegpafnrx
hold: null
hold_until: null
created_at: 2026-11-12T01:09:34.874Z
updated_at: 2026-11-12T01:09:35.925Z
started_at: 2026-11-12T01:09:35.550Z
closed_at: 2026-11-12T01:09:35.924Z
close_reason: "Fixed in fd731bd and fb335ec: planning refuses never, declared or inherited, on every step of a process that runs in the item scopes of a mapped composite, directly or through scalar composites (validate_reuse_policies mapped_composite, threaded by load_plan_bundle and build_plan); run-process applies it before the dry-run return and before writing the run directory. Honouring it was not clean: it would re-enter every completed item scope on every launch through the mapped dispatch and its spend reservation. Confirmed by test_never_under_a_mapped_composite_is_refused_before_the_run_starts (8 cases, all failed on dee3914 with the launch and dry run exiting 0) and test_a_validated_bundle_load_refuses_never_under_a_mapped_composite_and_a_viewer_does_not (DID NOT RAISE on dee3914)."
resolution: null
duplicate_of: null
---
Medium. src/metaproc/engine/validation.py:157-178; src/metaproc/commands/run_process.py:4353-4390; src/metaproc/docs/metaproc-design.md:1386-1396.

Review: https://github.com/jlevy/metaproc/pull/121#pullrequestreview-5866221123
PR: #121, reviewed head dee3914e5e001aace3abc728d81b0c6f859845e1
