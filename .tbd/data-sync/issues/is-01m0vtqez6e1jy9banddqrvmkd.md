---
type: is
id: is-01m0vtqez6e1jy9banddqrvmkd
title: Use the explicit GCP project for gcp-run artifact uploads
kind: bug
status: closed
priority: 1
version: 6
labels:
  - gcp
dependencies: []
created_at: 2026-08-25T06:46:36.760Z
updated_at: 2026-08-25T17:01:15.351Z
closed_at: 2026-08-25T17:01:15.351Z
close_reason: The explicit project path passes focused tests, full verification, exact-head public CI, and private downstream dispatch validation. Exact downstream run identities are maintained outside this public repository.
resolution: null
duplicate_of: null
---
gcp run requires METAPROC_GCP_PROJECT for Batch configuration but its wheel/workspace upload path constructed storage.Client() without that project. A valid service-account ADC key without project_id therefore failed before Batch submission. Thread the explicit project through artifact upload helpers and prove the real dispatch path.

## Notes

The explicit GCP project is threaded through generic wheel and workspace upload paths with no ambient fallback. Focused tests, full verification, and exact-head public CI passed. Private downstream canary identities are maintained outside this repository.
