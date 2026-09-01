---
type: is
id: is-01m1d5ftr29jb25fdjtskt748g
title: Isolate Pi live-check registration tests from ambient GCP token resolution
kind: bug
status: closed
priority: 2
version: 3
labels:
  - tests
  - auth
dependencies: []
parent_id: is-01m1d4p503de0qpgpq8tvc93v7
created_at: 2026-09-01T00:21:46.369Z
updated_at: 2026-09-01T00:32:15.478Z
closed_at: 2026-09-01T00:32:15.477Z
close_reason: "Included in PR #57; auth registration tests now isolate ambient GCP token resolution and the full verify gate passes."
resolution: null
duplicate_of: null
---
The two Pi registration-gate tests mock subprocess.run globally but do not stub resolve_gcp_token. With uncached ADC, google-auth consumes that mock before the asserted prompt call, so the exact make verify result depends on credential cache and test order. Stub token resolution at its module boundary and retain the prompt-call assertions.
