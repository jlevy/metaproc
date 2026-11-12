---
type: is
id: is-01m76mvawmjxkfjam9je57evn1
title: "PR #121 A12: four never doc statements are off"
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
created_at: 2026-11-12T01:09:41.907Z
updated_at: 2026-11-12T01:09:42.916Z
started_at: 2026-11-12T01:09:42.548Z
closed_at: 2026-11-12T01:09:42.915Z
close_reason: "Fixed in 6d0ae82: design 10.2 and the operator reference now say --from alone re-runs a selected never step, a satisfied override (metaproc override <step> --satisfied) wins over never, and a never manual step accepts its recorded acknowledgment; the changelog heading says every launch whose walk reaches it; items 1 and 4 described exact_inputs text and build_plan docstrings removed in 59397b6. Each statement is pinned by a test: test_from_alone_reruns_a_never_step_it_selects, test_a_satisfied_override_wins_over_never, test_never_on_a_manual_step_accepts_its_recorded_acknowledgment_again."
resolution: null
duplicate_of: null
---
Low. metaproc-design.md:1440-1441, :1427-1428; build_plan.py:749, :752; metaproc-operator-reference.md:564, :568-570; CHANGELOG.md:23.

Review: https://github.com/jlevy/metaproc/pull/121#pullrequestreview-5866221123
PR: #121, reviewed head dee3914e5e001aace3abc728d81b0c6f859845e1
