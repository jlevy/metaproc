---
type: is
id: is-01m0vtqez6e1jy9banddqrvmkd
title: Use the explicit GCP project for gcp-run artifact uploads
kind: bug
status: closed
priority: 1
version: 3
labels:
  - gcp
dependencies: []
created_at: 2026-08-25T06:46:36.760Z
updated_at: 2026-08-25T07:19:23.427Z
closed_at: 2026-08-25T07:19:23.426Z
close_reason: Fixed at 0b783f9 and verified by 79 focused tests, full make verify (4,270 passed, 8 skipped), five exact-head CI jobs, and live one- and two-ticker GCP Batch canaries shipping both wheel and workspace with service-account ADC lacking project_id.
resolution: null
duplicate_of: null
---
gcp run requires METAPROC_GCP_PROJECT for Batch configuration but its wheel/workspace upload path constructed storage.Client() without that project. A valid service-account ADC key without project_id therefore failed before Batch submission. Thread the explicit project through artifact upload helpers and prove the real dispatch path.

## Notes

Fixed at 0b783f9 and published as PR #42. Required project is threaded through the generic gcp-run wheel and workspace artifact paths; storage.Client receives it explicitly and there is no ambient fallback. Validation: 79 focused tests plus full standalone make verify (4,270 passed, 8 skipped; lint, type checking, audits, distribution, installed-wheel smoke). Awaiting exact-head PR CI and Trading live canary.
