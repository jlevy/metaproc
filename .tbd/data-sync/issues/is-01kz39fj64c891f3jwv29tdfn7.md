---
type: is
id: is-01kz39fj64c891f3jwv29tdfn7
title: "PR9-R9: Tolerate absent Batch runnable metadata"
kind: bug
status: closed
priority: 1
version: 2
labels: []
dependencies: []
parent_id: is-01kz36g3q9wbmhwnwcs170y1s3
created_at: 2026-08-03T07:47:43.937Z
updated_at: 2026-08-03T07:58:46.381Z
closed_at: 2026-08-03T07:58:46.381Z
close_reason: Fixed in 92dda0c, c28e06b, and 6995ac1 with regression coverage; full make verify passed 3,830/8, final GitHub matrix and Bugbot passed, and all associated review threads are resolved.
---
Cursor Bugbot found _run_id_from_job_metadata calls .get when environment.variables is None. Treat absent/None task_groups, runnables, environments, and non-mapping variables as unavailable metadata; preserve collision-safe identity-key fallback. Add an integration regression through gcp runs, address/resolve the thread, and rerun full verification and CI.
