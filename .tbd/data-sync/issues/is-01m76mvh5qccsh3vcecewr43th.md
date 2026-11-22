---
type: is
id: is-01m76mvh5qccsh3vcecewr43th
title: "PR #121 B4: never inherited by a mapped step of a child spec is refused only at launch, as a traceback after mkdir"
kind: bug
status: closed
priority: 3
version: 3
delegate: claude-code@spud10.local
labels: []
dependencies: []
parent_id: is-01m76msm7xe6a3n7dcegpafnrx
hold: null
hold_until: null
created_at: 2026-11-12T01:09:48.342Z
updated_at: 2026-11-12T01:09:49.660Z
started_at: 2026-11-12T01:09:49.094Z
closed_at: 2026-11-12T01:09:49.659Z
close_reason: "Fixed in fd731bd (kept by fb335ec): run-process loads the composite plan bundle with spec validation right after planning the root, before the dry-run return, cloud dispatch and run_dir.mkdir, and raises any child-spec error as ValidationError (exit 2). Confirmed by test_never_inherited_by_a_mapped_step_of_a_child_process_is_refused_before_the_run_starts: on dee3914 the launch raised a raw ValueError and left an empty run directory (scratch probe), and the dry run exited 0; both pass after."
resolution: null
duplicate_of: null
---
Low. src/metaproc/commands/run_process.py:6898, :6917; src/metaproc/viz_loader.py:82-97; src/metaproc/engine/build_plan.py:943-946.

Review: https://github.com/jlevy/metaproc/pull/121#pullrequestreview-5867092636
PR: #121, reviewed head dee3914e5e001aace3abc728d81b0c6f859845e1
