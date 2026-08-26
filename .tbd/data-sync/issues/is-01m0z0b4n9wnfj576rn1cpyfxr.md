---
type: is
id: is-01m0z0b4n9wnfj576rn1cpyfxr
title: Expose digest-pinned dispatch artifact staging
kind: feature
status: closed
priority: 1
version: 6
labels: []
dependencies: []
child_order_hints:
  - is-01m0z13a1jbepama425nmt3zbt
  - is-01m0z13aa2m7jk36t9jyqe1wmw
created_at: 2026-08-26T12:22:27.753Z
updated_at: 2026-08-26T12:48:17.632Z
closed_at: 2026-08-26T12:48:17.631Z
close_reason: Implemented, independently reviewed, committed, and pushed on PR 49 at 8e8a0f4. gcp stage is a thin stage-only primitive with immutable create-only GCS objects, digest-pinned RepoSyncPayload output, no Batch submission, no new orchestration state, and shared identity validation for gcp stage/run. Full local make verify and pre-push verify passed with 4,446 tests and 8 expected skips; all five GitHub CI jobs passed across Python 3.12-3.14, lint, and distribution.
resolution: null
duplicate_of: null
---
Full-cloud run-process already forwards digest-pinned wheel and workspace environment pairs and container bootstrap verifies them, while the lower-level gcp run command already owns their build/package/upload helpers. There is no operator-facing way to stage those existing artifacts without also submitting an unrelated one-shot Batch job, which makes exact current-checkout full-cloud development awkward and encourages private helper scripts. Add the smallest generic stage-only GCP CLI surface that reuses dispatch_artifacts, accepts the existing no-wheel/no-workspace/sync/sync-only controls, performs no Batch submission, and emits deterministic machine-readable wheel/workspace URI and SHA-256 pairs plus the dispatch identity. Test no-upload validation, explicit ignored-path inclusion, digest reporting, and failure propagation. Document it only in the public cloud-dispatch runbook and keep run-process as the sole orchestration API.

## Notes

Implementation and independent senior review complete. The stage-only primitive reuses existing packaging and RepoSyncPayload, submits no Batch job, owns no scheduler/state, uses create-only digest-pinned GCS objects, validates identities before work, and is documented across maintained public surfaces. All review findings mp-gukf, mp-vnpf, and mp-iod5 are fixed. Latest full make verify: 4,445 passed, 8 expected skips; lint, types, public hygiene, supply-chain, distributions, and installed-wheel smoke passed. One final public CLI registration regression also passes; pending commit, push, and GitHub CI.
