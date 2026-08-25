---
type: is
id: is-01m0w7pqnr8kq1cksat1zzb9hh
title: Pin no-workspace GCP runs to the baked uv environment
kind: bug
status: closed
priority: 1
version: 7
labels: []
dependencies: []
created_at: 2026-08-25T10:33:24.397Z
updated_at: 2026-08-25T17:01:14.350Z
closed_at: 2026-08-25T17:01:14.350Z
close_reason: No-workspace environment pins pass focused tests, full verification, exact-head public CI, and a downstream baked-source Batch resume. Exact downstream run and image evidence is maintained outside this public repository.
resolution: null
duplicate_of: null
---
A no-workspace metaproc gcp run can execute baked code that launches nested uv run --frozen commands. bootstrap_gcp_run leaves UV_PROJECT_ENVIRONMENT and UV_NO_SYNC unset in that mode, so uv searches for an absent workspace lock instead of using /opt/venv. Set the baked-environment pins when no workspace is shipped, preserve sync behavior for full shipped workspaces, and cover both modes.

## Notes

Implemented no-workspace environment pins with focused mode tests, full verification, and exact-head public CI. A live downstream Batch resume confirmed baked-source execution without provider requests; exact run and image evidence is maintained outside this public repository.
