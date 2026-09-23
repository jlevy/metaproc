---
type: is
id: is-01m0r7j1mjfegyspy6qvkvtk8g
title: "PR #29 review R2: preserve prompt snapshots per retry attempt"
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m0r7hpfe75sqfg3vecc7j8fr
created_at: 2026-08-23T21:13:53.041Z
updated_at: 2026-08-23T21:26:27.877Z
closed_at: 2026-08-23T21:26:27.877Z
close_reason: null
---
src/metaproc/commands/run_process.py:1498 and run_parallel.py:1422. Second-resolution filenames allow zero-backoff retries to overwrite earlier prompt snapshots. Add attempt identity and prove both scalar and fan-out prompt snapshots survive.
