---
type: is
id: is-01m0w7pqnr8kq1cksat1zzb9hh
title: Pin no-workspace GCP runs to the baked uv environment
kind: bug
status: closed
priority: 1
version: 4
labels: []
dependencies: []
created_at: 2026-08-25T10:33:24.397Z
updated_at: 2026-08-25T11:29:28.306Z
closed_at: 2026-08-25T11:29:28.305Z
close_reason: The no-workspace environment pins pass unit, full-suite, exact-head CI, immutable-image probe, and a live baked-source V2 Batch resume. Full shipped workspaces retain ordinary uv resolution.
resolution: null
duplicate_of: null
---
A no-workspace metaproc gcp run can execute baked code that launches nested uv run --frozen commands. bootstrap_gcp_run leaves UV_PROJECT_ENVIRONMENT and UV_NO_SYNC unset in that mode, so uv searches for an absent workspace lock instead of using /opt/venv. Set the baked-environment pins when no workspace is shipped, preserve sync behavior for full shipped workspaces, and cover both modes.

## Notes

Implemented at 1149e1fa77522730b7bdbda6102466e358e9090c with focused mode tests, standalone make verify (4287 passed, 8 skipped), and exact-head CI run 32838938027 (5/5 jobs). Final image sha256:77b79d292f94bb84578cd5c643fbc7d879436f07b247dd0f6f2b2ae10cffa034 then ran the baked V2 coordinator with --no-wheel --no-workspace; Batch job gtia-v2-gcp-stability-tgt-providerfree-resume-20260825-06 SUCCEEDED from Filestore with zero provider requests.
