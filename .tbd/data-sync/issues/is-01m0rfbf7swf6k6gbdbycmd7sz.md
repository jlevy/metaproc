---
type: is
id: is-01m0rfbf7swf6k6gbdbycmd7sz
title: Emit RFC3339 Cloud Logging watermarks from GCP run tailing
kind: bug
status: closed
priority: 1
version: 3
labels: []
dependencies: []
created_at: 2026-08-23T23:30:06.201Z
updated_at: 2026-08-23T23:37:04.666Z
closed_at: 2026-08-23T23:37:04.665Z
close_reason: "Fixed in 1987011 and verified by focused tests, the full 3.12/3.13/3.14 CI matrix, lint, and distribution checks; PR #30 is green."
resolution: null
duplicate_of: null
---
A real Batch run supplied datetime.__str__ output with a space separator to the Cloud Logging timestamp filter, causing repeated HTTP 400 errors and terminating the blocking monitor. Serialize datetime watermarks as RFC3339 and cover aware datetimes with a regression test.
