---
type: is
id: is-01m15c6zm3x0tek3n9hjbjx2hq
title: "PR #49 review R33: runtime coverage gaps never rendered"
kind: bug
status: closed
priority: 1
version: 2
labels: []
dependencies: []
parent_id: is-01m15c6ymbf8f0w71rmvjcyzt9
created_at: 2026-08-28T23:45:21.025Z
updated_at: 2026-08-29T02:44:38.567Z
closed_at: 2026-08-29T02:44:38.563Z
close_reason: "Fixed in 0bd0195: coverage-gaps section rendered, including when the task list is empty; asserted in the dom lifecycle test."
resolution: null
duplicate_of: null
---
src/metaproc/metabrowser_plugin/plugin/domain_views.js:404 renderRuntimeTaskProjection ignores projection.coverage_gaps, so a run with absent declared state renders like a fully covered run in the only shipped consumer. Fix: render a coverage-gaps section and assert it in tests/dom lifecycle test.
