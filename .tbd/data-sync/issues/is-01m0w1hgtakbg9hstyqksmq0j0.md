---
type: is
id: is-01m0w1hgtakbg9hstyqksmq0j0
title: Resolve GCP secrets inside containers so Batch agent logs never hold plaintext
kind: bug
status: in_progress
priority: 0
version: 2
labels:
  - gcp
  - security
dependencies: []
created_at: 2026-08-25T08:45:42.089Z
updated_at: 2026-08-25T09:17:34.673Z
---
Live GCP validation showed Batch agent logs expanding Environment.secret_variables into plaintext values in the generated docker command line. Stop sending secret values through Batch secret_variables. Serialize only Secret Manager resource references in ordinary job env, hydrate them with the attached service account inside each Metaproc container before adapter/bootstrap use, redact all diagnostics, cover gcp-run/orchestrator/worker assembly and resolution, and require provider credential rotation outside this code change.

## Notes

Implemented on codex/gcp-container-secret-hydration: Batch specs carry only validated Secret Manager version refs; all three entrypoints hydrate atomically under an explicit runtime service account before bootstrap; dispatch rejects ambient target plaintext; resource diagnostics redact every dispatched target independent of name; obsolete Batch secret_variables compatibility surfaces are removed; architecture and runbooks updated. Validation: 4,287 passed, 8 skipped; ruff, basedpyright, browser checks, public hygiene, supply chain, links, and diff check green. Harmless reference-only canary proved the old image does not expose the marker in agent logs but also proved pre-wheel entrypoint changes require an immutable image rebuild. Commit, stacked PR, candidate-image probe, and successful live canary remain.
