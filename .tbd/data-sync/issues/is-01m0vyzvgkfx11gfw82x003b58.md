---
type: is
id: is-01m0vyzvgkfx11gfw82x003b58
title: Reject secret-bearing GCP runs without an explicit Batch service account
kind: bug
status: closed
priority: 1
version: 3
labels:
  - gcp
  - security
dependencies: []
created_at: 2026-08-25T08:01:06.066Z
updated_at: 2026-08-25T08:09:34.464Z
closed_at: 2026-08-25T08:09:34.447Z
close_reason: "Fixed in 57167a2: secret-bearing generic GCP runs now require an explicit Batch service account before artifacts or submission; CLI and builder regression tests, docs, 4,272-test local suite, and all five exact-head PR #42 CI jobs are green."
resolution: null
duplicate_of: null
---
Live GCP validation showed that metaproc gcp run accepted Secret Manager bindings with no METAPROC_GCP_SERVICE_ACCOUNT, submitted under the default Compute identity, and failed during Batch environment preparation. Fail before artifact upload or submission whenever registry or --secret bindings resolve and no explicit Batch identity is configured. Cover the CLI and pure job builder, and document the requirement.

## Notes

Observed 2026-08-25: gcp-stability-fintool-trends-20260825-01 failed before container startup because omitted METAPROC_GCP_SERVICE_ACCOUNT selected the default Compute SA, which lacked secretmanager.versions.access. Corrected job gcp-stability-fintool-trends-20260825-02 used metaproc-batch-runner@aitradearena.iam.gserviceaccount.com and succeeded through one live Trends request. TDD coverage and early validation are implemented locally on PR #42.
