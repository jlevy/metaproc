---
type: is
id: is-01kz36gkk3x9vvf5gs6n6fq98f
title: "PR #9 review PR9-R3: validate timestamped allocator output"
kind: bug
status: closed
priority: 2
version: 3
labels:
  - pr-review
  - pr-9
dependencies: []
parent_id: is-01kz36g3q9wbmhwnwcs170y1s3
created_at: 2026-08-03T06:55:52.418Z
updated_at: 2026-08-03T07:00:44.139Z
closed_at: 2026-08-03T07:00:44.138Z
close_reason: "Fixed: timestamped allocation now strictly parses the supported strif shape, retries up to three times for the transient whole-second omission, and raises RuntimeError on persistent unsupported output. Deterministic recovery/failure and width tests pass."
---
Formal review PR9-R3 (Medium), PR #9. src/metaproc/ids.py:208. strif can omit the fractional separator at an exact whole second; blind hyphen replacement emits an invalid timestamped typed ID. Strictly parse, retry a bounded number of times, fail loudly on persistent invalid output, and add deterministic recovery/failure tests.
