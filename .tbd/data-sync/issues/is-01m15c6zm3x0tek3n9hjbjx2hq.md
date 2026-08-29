---
type: is
id: is-01m15c6zm3x0tek3n9hjbjx2hq
title: "PR #49 review R33: runtime coverage gaps never rendered"
kind: bug
status: open
priority: 1
version: 1
labels: []
dependencies: []
parent_id: is-01m15c6ymbf8f0w71rmvjcyzt9
created_at: 2026-08-28T23:45:21.025Z
updated_at: 2026-08-28T23:45:21.025Z
---
src/metaproc/metabrowser_plugin/plugin/domain_views.js:404 renderRuntimeTaskProjection ignores projection.coverage_gaps, so a run with absent declared state renders like a fully covered run in the only shipped consumer. Fix: render a coverage-gaps section and assert it in tests/dom lifecycle test.
