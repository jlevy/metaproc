---
type: is
id: is-01m0z0b4n9wnfj576rn1cpyfxr
title: Expose digest-pinned dispatch artifact staging
kind: feature
status: in_progress
priority: 1
version: 5
labels: []
dependencies: []
child_order_hints:
  - is-01m0z13a1jbepama425nmt3zbt
  - is-01m0z13aa2m7jk36t9jyqe1wmw
created_at: 2026-08-26T12:22:27.753Z
updated_at: 2026-08-26T12:42:57.716Z
---
Full-cloud run-process already forwards digest-pinned wheel and workspace environment pairs and container bootstrap verifies them, while the lower-level gcp run command already owns their build/package/upload helpers. There is no operator-facing way to stage those existing artifacts without also submitting an unrelated one-shot Batch job, which makes exact current-checkout full-cloud development awkward and encourages private helper scripts. Add the smallest generic stage-only GCP CLI surface that reuses dispatch_artifacts, accepts the existing no-wheel/no-workspace/sync/sync-only controls, performs no Batch submission, and emits deterministic machine-readable wheel/workspace URI and SHA-256 pairs plus the dispatch identity. Test no-upload validation, explicit ignored-path inclusion, digest reporting, and failure propagation. Document it only in the public cloud-dispatch runbook and keep run-process as the sole orchestration API.

## Notes

Implementation and independent senior review complete. The stage-only primitive reuses existing packaging and RepoSyncPayload, submits no Batch job, owns no scheduler/state, uses create-only digest-pinned GCS objects, validates identities before work, and is documented across maintained public surfaces. All review findings mp-gukf, mp-vnpf, and mp-iod5 are fixed. Latest full make verify: 4,445 passed, 8 expected skips; lint, types, public hygiene, supply-chain, distributions, and installed-wheel smoke passed. One final public CLI registration regression also passes; pending commit, push, and GitHub CI.
