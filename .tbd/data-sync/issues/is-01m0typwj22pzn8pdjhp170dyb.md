---
type: is
id: is-01m0typwj22pzn8pdjhp170dyb
title: "PR #38 review 4: preserve attached GCP identity over stale base64 credentials"
kind: bug
status: closed
priority: 2
version: 3
labels: []
dependencies: []
parent_id: is-01m0typ5swnqc9v7gee2ymkjs9
created_at: 2026-08-24T22:36:57.793Z
updated_at: 2026-08-24T22:56:48.519Z
closed_at: 2026-08-24T22:56:48.519Z
close_reason: Fixed in 809fccc; exact-head CI run 32786763844 passed all five jobs and disposition published at issuecomment-5402607487.
resolution: null
duplicate_of: null
---
Review finding 4 at issuecomment-5402359572. src/metaproc/cloud/gcp/gcp_credentials.py:55 no longer recognizes a mounted Filestore GCP host, so non-Batch GCE can materialize GCP_CREDENTIALS_BASE64 over the attached service account despite docs saying it will not. Restore a semantically named attached-identity signal or correct the behavior and docs with tests.
