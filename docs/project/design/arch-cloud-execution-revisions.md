# Architecture: Cloud Execution: Revision History

Authoring revisions of
[arch-cloud-execution.md](../../../src/metaproc/docs/arch-cloud-execution.md), kept as a
project record. These are not releases; for what shipped when, see
[CHANGELOG.md](../../../CHANGELOG.md) and [the release notes](../releases/).

Approximate mapping: rev2i and earlier predate v0.2.0; rev2j through rev2l correspond to
v0.2.1; rev2m and rev2n to v0.3.0; rev2o was unreleased when the history was moved here.

## Revisions

### rev11 (2026-08-26)

Documented `gcp run` as single-task placement for either an arbitrary command or one
complete local-backend DAG. Distinguished that topology from multi-VM `gcp-worker`
fan-out and recorded the single-host placement required by mapped composites.

### rev10 (2026-08-24)

Replaced duplicated authentication-pool fields on `OrchestratorDispatchConfig` with the
shared `AuthPoolFlags` payload already used by worker dispatch, and removed the
completed consolidation item from Potential Improvements.

### rev9 (2026-08-24)

Clarified that `run-process --cloud` is the application-level cloud API and `gcp run` is
a lower-level single-task primitive.
Recorded the planned `--orchestrator` and `--worker` placement axes, a run-wide worker
placement for the first implementation, the resolved-topology/provider boundary, and the
state-transport gate required before split-locus execution can be supported.

### rev8 (2026-08-24)

Removed the unsupported laptop-orchestrated hybrid topology, split-tree validation and
recovery commands, and the persistent gateway command family.
The supported paths are now local execution, full-cloud `run-process --cloud`, and
one-shot `gcp run`; filesystem-oriented commands operate on one locally visible run
tree.

### rev7 (2026-08-09)

Release-readiness synchronization:

- Corrected the arbitrary-command path to describe digest verification, installation
  into `/opt/venv`, and workspace extraction without a clone.
- Documented URI and digest forwarding for both cloud execution paths and generic
  workspace-package installation.
- Removed the stale Prefect module inventory and open question after verifying that no
  Prefect execution path exists in the package.
- Synchronized the container bootstrap inventory with its current eight-step contract.

### rev6 (2026-08-02)

Exact typed run identity:

- Documented readable `metaproc-run-id` versus collision-resistant `metaproc-run-key`
  labels on worker and orchestrator jobs.
- Updated monitoring to describe exact lookup, hash-verified mixed-generation jobs, the
  fully legacy fallback, and exact identity recovery/grouping in `gcp runs`.
- Documented exact local status identity resolution for process-subdirectory layouts.
- Added the shared run-identity helpers to the Batch utility inventory.

### rev5 (2026-05-23)

Maintenance revision via `tbd shortcut revise-architecture-doc`:

- Added standard arch-doc frontmatter and maintenance header.
- Normalized H1 to `Architecture: cloud execution`.
- Fixed `LaunchBackend` protocol signature (`name` is a `@property`; method signatures
  now match `runpool/backend.py:177`).
- Renamed `OrchestratorDispatchConfig.process_dir_rel` to `process_spec_rel`
  (`orchestrator_dispatch.py:48`).
- Added `initial_concurrency` and auth-pool passthrough fields to both
  `WorkerDispatchConfig` and `OrchestratorDispatchConfig`.
- Updated the credential registry to use `SecretRefSet` (`dispatch/secret_refs.py`) and
  added `CODEX_CREDS_JSON` row.
- Corrected §2.9 pre-flight: parameter is `needs_gcloud` (not `needs_gcp`), gated on
  `backend == "local"`; added `run_cloud_preflight()` mention.
- Updated §3.4 worker entrypoint: `METAPROC_PROCESS_SPEC` is primary env var (with
  `METAPROC_PROCESS_DIR` as legacy fallback); documented auth-pool env var forwarding.
- Added `billing.py` and `prefect_flow.py` to §3.14 module summary.
- Added §3.11 `METAPROC_GCP_SECRET_CODEX_CREDS` env var.
- Added Future Considerations section with open questions and potential improvements.

### rev1 (2026-04-12)

Initial extraction from arch-metaproc-core.md (rev2f, section 21). Restructured into
cloud-generic architecture (section 2) and GCP-specific implementation (section 3).
Added AWS placeholder (section 4).

### rev2 (2026-04-17)

Runtime/doc sync refresh for the current branch:

- documented the filesystem-first resume contract beyond `run-config.yaml`, including
  orchestrator leases, dispatch manifests, claim registries, and scale files
- updated container bootstrap to describe bundled `example_plugin`, sparse clone
  fallback, editable install, and optional `arena` bootstrap
- refreshed worker/orchestrator sections to match current GCP behavior and command
  surface, including `gcp scale`, `gcp remote-run`, and the broader operator tooling
- corrected secret-handling and auth-token details: Secret Manager is required for
  `GH_TOKEN` injection, and the helper is `resolve_gcp_token()`

### rev3 (2026-04-19)

Claude Code CLI Personal-Plan auth on GCP Batch (the original design):

- **§2.8 Container Bootstrap Contract**: added step 7, the adapter `bootstrap(home)`
  hook for credential files not safe to keep as env vars for the job lifetime.
- **§3.1 Infrastructure Components**: Secrets bullet now cites the Claude Code CLI
  Personal-Plan OAuth blob alongside `GH_TOKEN`.
- **§3.2 Container Bootstrap**: documents the adapter-bootstrap invocation (Claude Code
  CLI writes `~/.claude/.credentials.json` from `CLAUDE_CODE_CREDS_JSON` and unsets the
  env var).
- **§3.10 Secret Manager Integration**: generalized from GH_TOKEN-only to the typed
  secret-reference registry; added the two-hop Keychain → Secret Manager →
  adapter-bootstrap flow for the Claude credential; cross-links to credential-setup.md
  and cloud-dispatch.runbook.md.
- **§3.11 GCP Configuration Environment Variables**: added
  `METAPROC_GCP_SECRET_CLAUDE_CREDS`.

### rev4 (2026-04-19)

`metaproc gcp run` arbitrary-command dispatch primitive:

- **§3.14 Module Summary**: added `dispatch_artifacts.py`, `gcp_run_dispatch.py`,
  `gcp_run_entrypoint.py`, `gcp_run_logs.py`.
- **§3.15 `metaproc gcp run`**: new section documenting the primitive alongside the
  orchestrator/worker model: pipeline, blocking semantics, and why it’s a separate
  primitive (no lease, no claims, no dispatch manifest).

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
