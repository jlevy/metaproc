---
type: is
id: is-01kz38sfh2kerp5cn8k2kqgfk8
title: "PR9-R8: Resolve exact local status identity from run metadata"
kind: bug
status: closed
priority: 1
version: 2
labels: []
dependencies: []
parent_id: is-01kz36g3q9wbmhwnwcs170y1s3
created_at: 2026-08-03T07:35:40.322Z
updated_at: 2026-08-03T07:58:46.374Z
closed_at: 2026-08-03T07:58:46.374Z
close_reason: Fixed in 92dda0c, c28e06b, and 6995ac1 with regression coverage; full make verify passed 3,830/8, final GitHub matrix and Bugbot passed, and all associated review threads are resolved.
---
Cursor Bugbot found that local gcp status may target runs/<RUN_ID>/<process>, where run_dir.name is the process folder. Resolve the immutable RUN_ID from the canonical local run config/metadata, retain a safe path fallback for older layouts, add process-subdirectory regression coverage, update any affected docs, and address/resolve the GitHub thread.
