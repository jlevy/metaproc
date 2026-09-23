---
type: is
id: is-01kz383sv4wytkr8qzecp0gwhz
title: "PR9-R7: Include verified legacy workers in exact run lookup"
kind: bug
status: closed
priority: 1
version: 2
labels: []
dependencies: []
parent_id: is-01kz36g3q9wbmhwnwcs170y1s3
created_at: 2026-08-03T07:23:49.987Z
updated_at: 2026-08-03T07:58:46.364Z
closed_at: 2026-08-03T07:58:46.363Z
close_reason: Fixed in 92dda0c, c28e06b, and 6995ac1 with regression coverage; full make verify passed 3,830/8, final GitHub matrix and Bugbot passed, and all associated review threads are resolved.
---
Cursor Bugbot identified a mixed-generation resume gap: once an exact-key job exists, _query_jobs_by_run_id returns before considering older unkeyed workers from the same run. Query the readable label as well, but merge only unkeyed jobs whose structured METAPROC_VARS RUN_ID exactly matches the requested run and verifies against its identity key. Add positive mixed-generation and negative readable-collision coverage; address and resolve the GitHub thread.
