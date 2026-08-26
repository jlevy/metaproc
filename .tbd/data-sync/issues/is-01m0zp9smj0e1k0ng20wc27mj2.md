---
type: is
id: is-01m0zp9smj0e1k0ng20wc27mj2
title: Fail closed when gcp run implies Filestore without a server
kind: bug
status: in_progress
priority: 0
version: 2
labels:
  - gcp
  - filestore
  - durability
dependencies: []
created_at: 2026-08-26T18:46:12.369Z
updated_at: 2026-08-26T18:50:49.833Z
---
gcp run defaults RUNS_DIR to /mnt/filestore/runs even when METAPROC_GCP_FILESTORE_SERVER is absent. In that state the Batch job contains no NFS mount and writes an ephemeral local directory whose path looks durable; a later VM cannot resume or publish it. Refuse before workspace/wheel upload unless --no-filestore is explicit. Add a CLI regression proving the dispatcher is not called on the refused path and preserve explicit ephemeral execution.

## Notes

Implemented uncommitted in codex/complete-viz-projection: gcp run now rejects default Filestore placement when METAPROC_GCP_FILESTORE_SERVER is unset before artifact shipping or dispatch; explicit --no-filestore remains an ephemeral opt-out. Updated focused CLI tests and generic cloud-dispatch runbook. Validation: 52 focused tests passed; Ruff passed; BasedPyright 0 errors/warnings; Flowmark check passed; CLI help verified.
