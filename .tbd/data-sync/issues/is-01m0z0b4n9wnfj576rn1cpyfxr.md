---
type: is
id: is-01m0z0b4n9wnfj576rn1cpyfxr
title: Expose digest-pinned dispatch artifact staging
kind: feature
status: open
priority: 1
version: 1
labels: []
dependencies: []
created_at: 2026-08-26T12:22:27.753Z
updated_at: 2026-08-26T12:22:27.753Z
---
Full-cloud run-process already forwards digest-pinned wheel and workspace environment pairs and container bootstrap verifies them, while the lower-level gcp run command already owns their build/package/upload helpers. There is no operator-facing way to stage those existing artifacts without also submitting an unrelated one-shot Batch job, which makes exact current-checkout full-cloud development awkward and encourages private helper scripts. Add the smallest generic stage-only GCP CLI surface that reuses dispatch_artifacts, accepts the existing no-wheel/no-workspace/sync/sync-only controls, performs no Batch submission, and emits deterministic machine-readable wheel/workspace URI and SHA-256 pairs plus the dispatch identity. Test no-upload validation, explicit ignored-path inclusion, digest reporting, and failure propagation. Document it only in the public cloud-dispatch runbook and keep run-process as the sole orchestration API.
