---
type: is
id: is-01m15c70334jkh68agcxr0pnqp
title: "PR #49 review R34: plugin entry-point rediscovery per viz-model request"
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m15c6ymbf8f0w71rmvjcyzt9
created_at: 2026-08-28T23:45:21.506Z
updated_at: 2026-08-29T02:44:39.278Z
closed_at: 2026-08-29T02:44:39.275Z
close_reason: "Fixed in 0bd0195: ensure_plugins_loaded() is idempotent per process; both sidekick call sites use it."
resolution: null
duplicate_of: null
---
src/metaproc/metabrowser_plugin/sidekick.py:187-193 calls discover_and_load_plugins() unconditionally per HTTP request on a hot polled route, rescanning entry points and swapping the module-global registry. Fix: idempotent process-level bootstrap.
