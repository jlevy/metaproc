---
type: is
id: is-01m0r6ycjrpwj9etxnd0vxhcrr
title: "PR #29 review R2: Preserve per-attempt prompt snapshots"
kind: bug
status: closed
priority: 3
version: 3
labels: []
dependencies: []
parent_id: is-01m0r6hbjy0n7q0b58awfv7den
created_at: 2026-08-23T21:03:08.887Z
updated_at: 2026-08-23T21:17:51.388Z
closed_at: 2026-08-23T21:17:51.388Z
close_reason: Fixed in e14390a; focused tests, make verify, and final PR CI passed.
---
PR #29 review finding R2 (Low). src/metaproc/commands/run_process.py:1498 and src/metaproc/commands/run_parallel.py:1422: same-second retries overwrite prior prompt snapshots. Add attempt numbers to prompt filenames and prove both survive.
