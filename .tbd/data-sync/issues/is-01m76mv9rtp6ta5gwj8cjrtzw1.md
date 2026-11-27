---
type: is
id: is-01m76mv9rtp6ta5gwj8cjrtzw1
title: "PR #121 A11: ReusePolicy defined three times"
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
created_at: 2026-11-12T01:09:40.761Z
updated_at: 2026-11-12T01:09:41.733Z
started_at: 2026-11-12T01:09:41.364Z
closed_at: 2026-11-12T01:09:41.732Z
close_reason: "Fixed in fd731bd: ReusePolicy is defined once in metaproc.models.authored; StepDetails.reuse_policy and ResolvedStep.reuse_policy use it, and viz/project.py drops its copy, _VALID_REUSE_POLICIES and the cast, passing the value through. Confirmed by basedpyright (0 errors) and the viz and model tests (test_viz_project, test_viz_models, test_models)."
resolution: null
duplicate_of: null
---
Low. src/metaproc/models/authored.py:62, src/metaproc/viz/project.py:46-47, src/metaproc/models/viz.py:194.

Review: https://github.com/jlevy/metaproc/pull/121#pullrequestreview-5866221123
PR: #121, reviewed head dee3914e5e001aace3abc728d81b0c6f859845e1
