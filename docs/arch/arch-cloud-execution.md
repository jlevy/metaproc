---
title: "Architecture: Cloud Execution"
description: "Cloud-execution subsystem: GCP Batch dispatch, worker entrypoints, container lifecycle, cross-host coordination."
author: metaproc team
status: Approved
---
# Architecture: Cloud Execution

**Date:** 2026-04-12 (last updated 2026-08-09) **Status:** Approved

> **Maintenance**: This is a maintained architecture doc.
> Revise via `tbd shortcut revise-architecture-doc` (which prompts you to verify content
> against current code, then add a “Future Considerations” section).
> When you make non-trivial changes, bump the **last updated** date above.
> The full arch-doc index lives in
> [development.md § Architecture docs](../development.md#architecture-docs).
> 
> Companion docs (in `docs/arch/`): [arch-metaproc-core](arch-metaproc-core.md),
> [arch-runpool](arch-runpool.md), [arch-cloud-execution](arch-cloud-execution.md),
> [arch-authentication](arch-authentication.md),
> [arch-claude-code-harness](arch-claude-code-harness.md),
> [arch-testing](arch-testing.md).

For the overall metaproc framework design, see
[arch-metaproc-core.md](arch-metaproc-core.md); for the run pool process management
subsystem, see [arch-runpool.md](arch-runpool.md).

## 1. Background and Requirements

### 1.1 Problem

Local execution has inherent scaling limits: a single machine constrains concurrent
agent invocations by CPU, memory, and network bandwidth.
Running 500+ fan-out items locally takes hours even with aggressive concurrency, and
memory pressure forces the adaptive pool to shed slots.

Cloud execution solves this by distributing fan-out items across multiple VMs, each
running its own run pool.
The challenge is doing this without introducing a cloud-specific execution model — the
same process spec and CLI commands must work identically whether run locally or in the
cloud.

### 1.2 Requirements

- **Same CLI, different topology:** `run-process` on a laptop and `run-process` on a
  cloud VM must produce identical results.
  The cloud layer moves *where* commands run, not *what* they run.
- **Shared filesystem for state:** all VMs in a cloud run share a single filesystem so
  that resume, status, and publication semantics are unchanged.
- **No cloud dependency for correctness:** the framework never requires cloud APIs to
  determine whether a step is complete, an item succeeded, or a run is resumable.
  Cloud APIs are used for dispatch and monitoring, not for state.
- **Provider-specific naming:** cloud commands use provider-specific names (e.g.,
  `metaproc gcp`) rather than a generic `cloud` abstraction, because operational
  commands are inherently provider-specific.
- **Infrastructure as configuration:** the framework never hardcodes VM names, NFS
  shares, or deployment topology.
  All infrastructure references are environment variables or CLI flags.

## 2. Cloud-Generic Architecture

The execution model and contracts below apply regardless of cloud provider;
provider-specific implementation details are in the provider sections.

### 2.1 Two-Tier Execution Model

Cloud execution uses a two-tier model: an **orchestrator** VM runs the DAG walker
(`run-process`), and **worker** VMs run fan-out items (`run-parallel`).

```text
run-process --cloud
  +-- Orchestrator VM (non-preemptible)
        +-- run-process --backend <provider>-worker (no --cloud, avoids recursion)
              |-- Code steps: execute locally on orchestrator
              +-- Fan-out steps: dispatch to worker VMs
                    |-- Worker VM 0 (preemptible) -> run-parallel --backend local
                    |-- Worker VM 1 (preemptible) -> run-parallel --backend local
                    +-- Worker VM N (preemptible) -> run-parallel --backend local
```

The orchestrator uses a non-preemptible VM to avoid losing the DAG coordinator.
Workers default to preemptible/spot VMs for cost efficiency — killed items are retryable
on resume.

### 2.2 Execution Chain by Topology

The same process spec runs identically across local, hybrid, and full cloud topologies.
The orchestrator and run pool move between machines; the adapter subprocesses at the
bottom of the stack are always the same.

| Layer | Local | Hybrid | Full Cloud (`--cloud`) |
| --- | --- | --- | --- |
| Entry | `metaproc run-process` on laptop | `metaproc run-process` on laptop | `metaproc run-process --cloud` on laptop |
| Orchestrator | `run-process` on laptop | `run-process` on laptop | `run-process` on orchestrator VM |
| Code steps | Run locally on laptop | Run locally on laptop | Run locally on orchestrator VM |
| Fan-out dispatch | `run-parallel` on laptop | `dispatch_to_workers()` -> N worker VMs | `dispatch_to_workers()` -> N worker VMs |
| Item execution | RunPool + LocalBackend on laptop | RunPool + LocalBackend on each worker VM | RunPool + LocalBackend on each worker VM |
| Adapter subprocess | `pi`/`claude`/`gemini` on laptop | `pi`/`claude`/`gemini` on worker VM | `pi`/`claude`/`gemini` on worker VM |
| Shared state | Local disk | NFS | NFS |

### 2.3 Fan-Out Backend Dispatch

Fan-out steps in `run-process` dispatch through a backend selected via `--backend`:

| Backend | Flag | Mechanism |
| --- | --- | --- |
| `local` | `--backend local` (default) | `RunPool` subprocess pool via `run-parallel` |
| `gcp-worker` | `--backend gcp-worker` | Partition items across N worker VMs via GCP Batch |

**Note on backend abstraction:** `local` is a registered `LaunchBackend` implementation
(see section 2.5) in the backend registry (`runpool/registry.py`). Cloud worker backends
are different — they are multi-VM dispatch modes handled directly in `run-process`, not
`LaunchBackend` implementations.
A cloud worker backend partitions items across N VMs, each of which runs
`run-parallel --backend local` internally.

If a second cloud provider were added, a new worker dispatch implementation would
register alongside `gcp-worker` in the `run-process` dispatch logic.

### 2.4 Filesystem-First Resume Contract

Authoritative run state lives only on the run filesystem — local disk for full-local
runs, shared NFS for all cloud-backed modes.
Full per-artifact schemas and lifecycles live in
[artifact-catalog.md](../artifact-catalog.md); this section covers the dispatch-relevant
subset.

**`run-config.yaml`** (`{run_dir}/.state/run-config.yaml`): written at run creation time
with immutable run identity (process name, run_id, backend, variant, git SHA, creation
timestamp).
On resume, validated against current launch parameters — process identity and
run directory must match.
Cross-topology resume (e.g., hybrid to full cloud) is allowed because they share the
same authoritative filesystem.

**`orchestrator-lease.yaml`** (`{run_dir}/.state/orchestrator-lease.yaml`): prevents two
orchestrators from walking the same DAG at once.
The lease records owner identity and a heartbeat; stale leases can be taken over
explicitly.

**Cloud fan-out state** lives under the per-step state branch:

- `{run_dir}/.state/steps/{step_id}/dispatch-manifest.yaml` records submitted worker
  jobs so a resume can adopt live workers instead of blindly redispatching.
- `{run_dir}/.state/steps/{step_id}/worker-<id>/claimed-items.yaml` records per-worker
  item claims during live scale-up/reconciliation.
- `{run_dir}/.state/steps/{step_id}/scale-state.yaml` and `scale-override.yaml` capture
  the desired topology and operator caps for active worker pools.

Resume behavior: re-running `run-process` with the same `RUN_ID` skips completed steps
and items based on on-disk status records.
`run-config.yaml` prevents accidental collision between unrelated runs sharing a
directory.

### 2.5 LaunchBackend Protocol

The `LaunchBackend` protocol (`runpool/backend.py`) abstracts subprocess lifecycle
within a single machine or VM. It is used by `RunPool` for local execution; multi-VM
cloud dispatch is a separate concern handled in `run-process` (see section 2.3).

```python
class LaunchBackend(Protocol):
    @property
    def name(self) -> str: ...
    async def launch(self, prepared: PreparedLaunch, label: str = "") -> LaunchHandle
    async def poll(self, handle: LaunchHandle) -> int | None
    async def kill(self, handle: LaunchHandle, sig: int | None = None) -> None
    async def health(self, handle: LaunchHandle) -> HealthMetrics | None
    async def read_log_tail(self, handle: LaunchHandle, lines: int = 50) -> str
```

Supporting types:

- `PreparedLaunch` (frozen dataclass): `command`, `env`, `cwd`, `log_path`,
  `filter_log`, `metadata` (backend-specific context).
- `LaunchHandle` (frozen dataclass): `pid`, `external_id`, `backend_name`, `metadata`.
- `HealthMetrics` (frozen dataclass): `rss_bytes`, `descendants`, `log_bytes`.

Production implementation: `LocalBackend` (subprocess-based, with RSS/descendant
tracking). `MockBackend` is available for testing.

The `LaunchBackend` protocol and the backend registry (entry-point group
`metaproc.backends`) are fully provider-agnostic.
Adding a new local backend means implementing the 5-method protocol and registering it —
no changes to `engine/`, `runpool/`, or `models/` are required.

### 2.6 Provider Naming and Extensibility

The cloud layer uses provider-specific names rather than a generic `cloud` abstraction.

**CLI subcommand:** `metaproc gcp` (not `metaproc cloud`). The commands under a provider
subgroup are inherently provider-API-specific — they query provider batch APIs, stream
from provider logging, manage provider storage, etc.
A second cloud provider (e.g., AWS) would get its own subcommand (`metaproc aws`) with
provider-appropriate commands, rather than a single `cloud` subcommand that papers over
real operational differences.

**Framework-level abstraction:** The `LaunchBackend` protocol (section 2.5) is
provider-agnostic. Cloud execution uses a different model: provider-specific worker
dispatch is a multi-VM dispatch mode handled directly in `run-process`, not a
`LaunchBackend`. Each worker VM runs `run-parallel --backend local` internally, so the
`LaunchBackend` protocol operates at the subprocess level within each VM.

The naming hierarchy:

| Layer | Example | Scope |
| --- | --- | --- |
| Framework protocol | `LaunchBackend` | provider-agnostic subprocess lifecycle |
| Backend name | `local` | registered `LaunchBackend` implementation |
| CLI subgroup | `metaproc gcp` | provider-specific operational commands |
| Dispatch mode | `gcp-worker` | provider-specific multi-VM dispatch |

### 2.7 Persistent Infrastructure Decoupling

The framework does not depend on any specific deployment topology.
Persistent infrastructure (VMs, NFS shares, container registries) is external to
metaproc — the framework provides CLI commands that can be run anywhere.

Design principles for infrastructure dependencies:

- **No infra assumptions in the framework.** The framework never imports or depends on
  knowledge of specific VMs, storage instances, or persistent deployments.
  All infrastructure references are configuration, not code.
- **Configuration via environment variables.** All infrastructure parameters are
  configurable via env vars and overridable via CLI flags.
  No infrastructure names are hardcoded.
- **The browser is a read-only local tool.** The `serve` command reads filesystem
  artifacts and can be deployed anywhere without metaproc caring about the hosting.
- **Cloud commands are operational tools, not control plane.** Provider subcommands
  query and manage cloud resources but do not constitute a required control plane.
  A local `run-process` produces the same results as a cloud one.

### 2.8 Container Bootstrap Contract

All cloud providers share a container bootstrap sequence.
The concrete implementation is provider-specific, but the contract is:

1. Configure git identity and credential helper.
2. Use the bundled repo content from the image when possible, or sparse-clone the
   requested branch when a runtime branch override is needed.
3. Install the domain package(s) needed for plugin discovery.
4. Bootstrap any opt-in domain tooling required by the process.
5. Ensure the runs directory exists on the shared filesystem.
6. Write any adapter-specific configuration files from environment variables.
7. Invoke each adapter’s `bootstrap(home)` hook so adapters can materialize credential
   files that are not safe to carry as env vars for the full job lifetime (e.g., OAuth
   blobs bound from a secret store).
   Adapters that don’t need one leave the hook as the default no-op.

### 2.9 Cloud-Aware Pre-Flight Checks

The pre-flight check system (`engine/preflight.py`) includes cloud-specific checks that
run conditionally:

- **GCP auth**: resolves a GCP access token via `google.auth` (`resolve_gcp_token`).
  Only runs when `adapter_type == "pi-cli"`, the provider starts with `"vertex"`, and
  `backend == "local"` (cloud workers resolve tokens via the VM metadata server
  instead). Passed as `needs_gcloud=True` to `run_preflight()`.

Pre-flight checks are called from `run_parallel` before the first batch (skipped in
dry-run). Failures abort the run with `CLIError`. A separate `run_cloud_preflight()`
validates cloud-dispatch prerequisites (GCP project, service account, container image,
ADC, Filestore config) before `--cloud` submission.

### 2.10 Mount Path Standardization

All VM types (workers, orchestrators, monitoring hosts) mount the shared NFS at a
standardized path. Run trees live at `<mount_path>/runs/{run_id}/`. The mount path is a
container-level volume mount point, not subject to host-level path restrictions.

## 3. GCP Implementation

### 3.1 Infrastructure Components

- **Compute:** GCP Batch API for submitting and managing VM jobs.
- **Storage:** Filestore NFS for shared run state across all VMs.
- **Secrets:** Secret Manager for credential injection (e.g., `GH_TOKEN`, Claude Code
  CLI Personal-Plan OAuth blob).
  See §3.10.
- **Logging:** Cloud Logging for centralized log streaming.
- **Container:** Docker images with pre-installed metaproc and agent CLIs.

### 3.2 Container Bootstrap (`container_bootstrap.py`)

Shared by worker and orchestrator entrypoints via
`bootstrap_container() -> BootstrapResult`:

1. Read `GH_TOKEN` once, remove it from the environment, and expose it only through a
   temporary askpass helper when a sparse clone needs authentication.
2. Install a current-branch metaproc wheel from `METAPROC_WHEEL_GCS` when set with its
   required `METAPROC_WHEEL_SHA256`, overriding any image-baked metaproc.
3. Acquire the consumer workspace: a `METAPROC_WORKSPACE_GCS` tarball when set, verified
   against its required `METAPROC_WORKSPACE_SHA256`; otherwise a sparse clone of
   `METAPROC_RUN_BRANCH` and `METAPROC_REPO_URL`, falling back to the workspace bundled
   into the image.
4. Editable-install the workspace packages named in the repo-sync payload so consumer
   plugin entry points resolve inside the container.
5. Run each `metaproc.container_bootstrap` entry-point hook so downstream images can
   bootstrap their own tooling.
6. Ensure `RUNS_DIR` exists.
7. Write `~/.pi/agent/models.json` from `METAPROC_PI_MODELS_JSON` if set.
8. Back in the worker and orchestrator entrypoints, invoke each registered adapter’s
   `bootstrap(home)` hook.
   The `ClaudeCodeCliAdapter` uses this to write `~/.claude/.credentials.json` (mode
   0600\) from `CLAUDE_CODE_CREDS_JSON` (see §3.10), then unsets the env var so the
   OAuth payload does not propagate to child processes.

### 3.3 Worker Dispatch (`worker_dispatch.py`)

`dispatch_to_workers()` partitions fan-out items across N worker VMs:

- **Partitioning**: round-robin distribution via `partition_items()`.
  `min(num_workers, total_items)` workers are created.
- **Job submission**: one GCP Batch job per worker, each running `worker_entrypoint.py`.
  Items are passed via `METAPROC_WORKER_ITEMS` (comma-separated) and
  `METAPROC_ITEM_CONTEXTS` (JSON array) env vars.
  Large item-context payloads spill to
  `{run_dir}/.state/steps/{step_id}/worker_payloads/worker-<id>-item-contexts.json` on
  Filestore and are loaded via `METAPROC_ITEM_CONTEXTS_FILE`.
- **Resume/adoption**: writes `{run_dir}/.state/steps/{step_id}/dispatch-manifest.yaml`
  after submission so later resumes can adopt already-running worker jobs.
- **Scaling/reconcile**: reads step-level `scale-state.yaml` and uses per-worker
  `claimed-items.yaml` registries to avoid duplicate work when the operator scales up an
  active fan-out step.
- **Polling**: async poll loop with configurable interval.
  On failure, reads NFS `runpool-status.yaml` for error detail (failure counts, kill
  reasons). During polling, reads NFS pool status for live progress
  (completed/failed/active counts).
- **Labels**: `metaproc-role=worker`, `metaproc-worker-id=N`, `metaproc-step=<step_id>`,
  plus the readable `metaproc-run-id=<sanitized_run_id>` and exact
  `metaproc-run-key=v1-<sha256_prefix>` identity pair.
- **Defaults**: `n2-highmem-8` machine type, 50 concurrency per worker, Spot VMs.

`WorkerDispatchConfig` (frozen dataclass): `gcp`, `num_workers`, `max_concurrency`,
`initial_concurrency`, `max_retries`, `poll_interval`, `spot`, `variant`,
`adapter_config_json`, `auth_flags` (`AuthPoolFlags`).

### 3.4 Worker Entrypoint (`worker_entrypoint.py`)

Unified container entrypoint for worker containers:

1. Read env vars (`METAPROC_WORKER_ITEMS`, `METAPROC_PROCESS_SPEC` (with
   `METAPROC_PROCESS_DIR` as legacy fallback), `METAPROC_STEP`, etc.). Auth-pool
   dispatch env vars (`METAPROC_AUTH_ACCOUNT`, `METAPROC_AUTH_BACKEND`,
   `METAPROC_AUTH_FALLBACK_POLICY`, `METAPROC_AUTH_INCLUDE_LABELS`,
   `METAPROC_AUTH_EXCLUDE_LABELS`) are forwarded as `--auth-*` flags when present;
   without them, the worker falls back to single-credential bootstrap.
2. Call `bootstrap_container()`.
3. Build and run:
   `python -m metaproc run-parallel <process_spec> --step <step> --items <items> --backend local [--auth-* flags]`.
4. Exit with `run-parallel`’s exit code.
   Outputs land on NFS.

### 3.5 Orchestrator Dispatch (`orchestrator_dispatch.py`)

`dispatch_orchestrator()` submits the entire process DAG as a single GCP Batch job:

- Container overrides the Dockerfile ENTRYPOINT to run `orchestrator_entrypoint.py`.
- Uses a STANDARD VM (not Spot) to avoid preemption.
- Forwards all GCP config so the orchestrator can dispatch worker Batch jobs.
- `RUNS_DIR` is set to `<filestore_mount_path>/runs` (e.g., `/mnt/filestore/runs`) when
  Filestore is configured.
  This is the run root, not the bare NFS mount point.
- Labels: `metaproc-role=orchestrator`, the readable
  `metaproc-run-id=<sanitized_run_id>`, and the exact
  `metaproc-run-key=v1-<sha256_prefix>`.
- Polls in a while-True loop until terminal state.

`OrchestratorDispatchConfig` (frozen dataclass): `gcp`, `process_spec_rel`, `variables`,
`num_workers`, `worker_machine_type`, `max_concurrency`, `initial_concurrency`,
`spot_workers`, `variant`, `adapter_config`, `skip_steps`, `from_step`, `only_step`,
`force`, `continue_on_error`, `orchestrator_machine_type`, `max_duration_s` (default
8h), `poll_interval`, plus auth-pool passthrough fields (`auth_account`, `auth_backend`,
`auth_fallback_policy`, `auth_include_labels`, `auth_exclude_labels`,
`auth_cross_quota_group`).

### 3.6 Orchestrator Entrypoint (`orchestrator_entrypoint.py`)

1. Read orchestrator env vars.
2. Call `bootstrap_container()`.
3. Let the process DAG materialize any roster/run inputs on NFS via ordinary in-DAG code
   steps.
4. Build and run:
   `python -m metaproc run-process <process_dir> --backend gcp-worker [all forwarded flags]`.
5. Does **not** pass `--cloud` to avoid infinite recursion.
6. Exit with `run-process`’s exit code.

### 3.7 GCP Batch Shared Utilities (`batch_backend.py`)

`batch_backend.py` provides shared configuration and helpers used by both
`worker_dispatch.py` and `orchestrator_dispatch.py` for submitting GCP Batch jobs.

`GCPBatchConfig` (frozen dataclass): `project`, `region`, `container_image`,
`machine_type`, `spot`, `boot_disk_gb`, `max_run_duration_s`, `service_account_email`,
`network`/`subnetwork`, `labels`, `secret_env_vars`, `runs_dir`,
`filestore_server`/`filestore_share`/`filestore_mount_path`, `queue_timeout_s`.

Utility functions: `sanitize_label()` (GCP-safe resource names and readable labels),
`run_identity_label()` / `run_identity_labels()` (fixed-width exact run locator plus
readable compatibility label), `_build_secret_env_vars()` (Secret Manager mapping from
env vars), `build_pi_models_json()` (rewrite pi CLI config for cloud),
`infer_compute_resource()` / `resolve_compute_resource()` / `build_compute_resource()`
(right-size container cgroup from VM machine type; see below),
`is_transient_api_error()` (retry classification).

#### Container compute_resource

GCP Batch defaults an unset `TaskSpec.compute_resource` to **2000 cpu_milli / 2000
memory_mib** regardless of the underlying VM. On `n2-highmem-8` (8 vCPU / 64 GiB) that
means the container’s cgroup gets ~3% of the host’s RAM, and long-running agent
workloads get OOM-killed by the cgroup well before the host runs out of anything.
The Phase 1c Qwen3 235B baseline hit this pattern explicitly — `metaproc.runpool`
correctly read `memory_ceiling=1` from the 2 GiB cgroup and cut concurrency, then
SIGKILL fired anyway.

`build_compute_resource(machine_type)` derives the right cgroup from the VM shape via
`infer_compute_resource(machine_type)`:

- Parses `<family>-<class>-<N>` (e.g. `n2-highmem-8`) and `custom-CPU-MEM`.
- Class ratios — `standard` 4 GiB/vCPU, `highmem` 8, `highcpu` 1, `megamem` 13,
  `ultramem` 28 — match GCE published shapes.
- Reserves `HOST_CPU_RESERVE_MILLI=500` and `HOST_MEMORY_RESERVE_MIB=1024` for the Batch
  agent + Docker daemon + host OS; the rest goes to the container.
- Operator overrides via `METAPROC_GCP_TASK_CPU_MILLI` and
  `METAPROC_GCP_TASK_MEMORY_MIB`.
- Unparsable / unsupported machine types log a loud warning and fall back to the legacy
  2000/2000 default; the warning makes the misconfiguration visible rather than silently
  re-creating the bug.

Both `worker_dispatch.py` and `orchestrator_dispatch.py` set
`TaskSpec.compute_resource = build_compute_resource(<machine_type>)` so every container
actually gets the VM’s capacity.
Confirm a live container’s effective limits with `gcp resources` (CLI) or by reading the
`[cgroup …]` block in the worker / orchestrator startup log emitted by
`metaproc.osutils.resource_context.log_resource_context`.

### 3.8 Monitoring Cloud Runs

- **`gcp status <target>`**: auto-detects local run directory or run-id string.
  Queries Batch API by job name (local) or both exact `metaproc-run-key` and readable
  `metaproc-run-id` (run-id).
  Local display resolves the immutable ID from `run-config.yaml`, then hash-verified job
  metadata, before a path fallback.
  When exact jobs exist, it adds only unkeyed legacy jobs whose structured `RUN_ID`
  verifies as the same run; fully legacy runs retain the readable-label fallback.
  Shows orchestrator \+ worker jobs with role, state, step, worker_id.
- **`gcp scale <target> --step <step>`**: updates desired worker topology for an active
  fan-out step by writing `scale-state.yaml` and, when possible, reconciling new worker
  jobs immediately.
- **`gcp logs <target>`**: streams logs from Cloud Logging.
  Auto-detects local dir or run-id and uses the same exact-first job resolution.
- **`gcp cancel <target>`**: cancels all running/queued Batch jobs.
  Auto-detects local dir or run-id and uses the same exact-first job resolution.
  Writes pool kill sentinel if local dir exists.
- **`gcp runs`**: lists metaproc runs across the project.
  Modern jobs group by `metaproc-run-key`; the command reads `RUN_ID` from their
  structured `METAPROC_VARS` metadata and accepts it only when its hash matches the key,
  preserving exact IDs for display and JSON output.
  Legacy jobs continue to group by readable label.
- **`gcp resources` / `gcp filestore` / `gcp archive` / `gcp cleanup`**: operator tools
  for cloud inventory, Filestore utilization, long-term run archiving, and terminal-job
  cleanup.
- **NFS progress polling**: during worker dispatch, the orchestrator reads
  `runpool-status.yaml` from NFS to report live progress (completed/failed/active).
- **NFS error extraction**: on worker failure, reads `runpool-status.yaml` for failure
  counts and per-process kill reasons.
- **`gcp remote <command...>`**: runs any metaproc command on the Filestore-connected
  gateway host via SSH/IAP. Bootstraps the full repo on first use
  (`_ensure_remote_repo()`). `--run-id <id>` expands to
  `/mnt/filestore/runs/<id>/<phase>` on the remote host.
  Primary use cases: `stats`, `status`, `pool status`, `tail`.
- **`gcp remote-run`**: launches `run-process` in a tmux session on a remote GCE host,
  so the session survives disconnects and can be reattached or cleaned up later.

### 3.9 GCP Mount Path

All GCP VM types (workers, orchestrators, browser host) mount the Filestore NFS share at
`/mnt/filestore` by default.
`RUNS_DIR` resolves to `<mount_path>/runs`, not the bare share root, so run trees live
at `/mnt/filestore/runs/{run_id}/`. The mount path is a container-level Volume mount
point set via the Batch API Volume spec, not subject to COS host-level path
restrictions.

### 3.10 Secret Manager Integration

Every credential delivered to a Batch job is injected via Secret Manager rather than as
a plaintext env var — plaintext would persist in the job spec returned by
`gcloud batch jobs describe`. The canonical registry is `SecretRefSet.all_known()` in
`metaproc.dispatch.secret_refs`, composing static refs with dynamic provider refs
aggregated from `metaproc.config.providers.gcp_secret_refs()`. The legacy
`GCP_SECRET_REFS` tuple in `cloud/gcp/batch_backend.py` is a back-compat alias.

Static refs (as of 2026-05-23):

| Plaintext env | Secret Manager env | Purpose |
| --- | --- | --- |
| `GH_TOKEN` | `METAPROC_GCP_SECRET_GH_TOKEN` | Private-repo access from Batch VMs |
| `CLAUDE_CODE_CREDS_JSON` | `METAPROC_GCP_SECRET_CLAUDE_CREDS` | Claude Code CLI Personal-Plan OAuth blob |
| `CODEX_CREDS_JSON` | `METAPROC_GCP_SECRET_CODEX_CREDS` | Codex CLI ChatGPT-OAuth blob |

Provider-specific refs (pi-cli API keys, etc.)
are added dynamically via `gcp_secret_refs()` in `metaproc.config.providers`; adding a
new provider credential there is sufficient, no edits to dispatch wiring needed.

`resolve_gcp_secret_ref()` enforces a uniform policy: if the plaintext env var is set
but the corresponding Secret Manager ref is not, dispatch fails up front rather than
leaking the value.

The Claude Code CLI OAuth blob is a two-hop credential: the operator pushes
`~/.claude/.credentials.json` (read from macOS Keychain) to Secret Manager via
`metaproc claude-auth push`; dispatch binds `METAPROC_GCP_SECRET_CLAUDE_CREDS` →
`CLAUDE_CODE_CREDS_JSON` as a Batch `secret_variables` entry; the
`ClaudeCodeCliAdapter.bootstrap(home)` hook (invoked by §2.8 / §3.2) materializes the
credential file on the worker and unsets the env var.
See [credential-setup.runbook.md](../runbooks/credential-setup.runbook.md) for the
operator setup flow and
[cloud-dispatch.runbook.md](../runbooks/cloud-dispatch.runbook.md) → *GCP Batch
(Personal Plan)* for the end-to-end dispatch recipe.

### 3.11 GCP Configuration Environment Variables

All GCP infrastructure parameters are configurable via env vars:

- `METAPROC_GCP_PROJECT` — GCP project ID
- `METAPROC_GCP_FILESTORE_SERVER` — Filestore IP or hostname
- `METAPROC_GCP_FILESTORE_SHARE` — Filestore share name
- `METAPROC_GCP_FILESTORE_MOUNT_PATH` — NFS mount path (default `/mnt/filestore`)
- `METAPROC_GATEWAY_HOST` — gateway host for `gcp remote`
- `METAPROC_GCP_SECRET_GH_TOKEN` — Secret Manager resource name for GH_TOKEN
- `METAPROC_GCP_SECRET_CLAUDE_CREDS` — Secret Manager resource name for the Claude Code
  CLI Personal-Plan OAuth blob (required when dispatching `variant=claude-code-cli` on
  Batch with the subscription credential)
- `METAPROC_GCP_SECRET_CODEX_CREDS` — Secret Manager resource name for the Codex CLI
  ChatGPT-OAuth blob
- `METAPROC_RUN_BRANCH` — git branch for container bootstrap
- `METAPROC_REPO_URL` — repo URL for container bootstrap

### 3.12 Vertex AI MaaS Integration

Third-party models in Vertex AI Model Garden (GLM-5, Kimi K2, etc.)
expose an OpenAI-compatible chat/completions endpoint.
The Pi CLI adapter connects to these via a custom `vertex-maas` provider configured in
`~/.pi/agent/models.json`:

```json
{
  "providers": {
    "vertex-maas": {
      "baseUrl": "https://aiplatform.googleapis.com/v1/projects/<PROJECT>/locations/global/endpoints/openapi",
      "api": "openai-completions",
      "apiKey": "<injected-by-metaproc-at-runtime>",
      "authHeader": true,
      "compat": { "supportsDeveloperRole": false },
      "models": [
        {
          "id": "zai-org/glm-5-maas",
          "name": "GLM-5 (Vertex AI MaaS)",
          "reasoning": true,
          "input": ["text"],
          "contextWindow": 200000,
          "maxTokens": 128000
        },
        {
          "id": "moonshotai/kimi-k2-thinking-maas",
          "name": "Kimi K2 Thinking (Vertex AI MaaS)",
          "reasoning": true,
          "input": ["text"],
          "contextWindow": 262144,
          "maxTokens": 262144
        }
      ]
    }
  }
}
```

Key configuration details (validated 2026-03-31):

- **Endpoint**: Must use the global endpoint (`aiplatform.googleapis.com`), not
  regional. Some models (e.g., GLM-5) are not available in all regions.
- **Model IDs**: Must include the publisher prefix (e.g., `zai-org/glm-5-maas`,
  `moonshotai/kimi-k2-thinking-maas`).
- **Auth**: The `apiKey` field is injected at runtime by metaproc via
  `google.auth.default()` auto-refreshing credentials (`metaproc[gcp]` extra).
  `"authHeader": true` sends it as `Authorization: Bearer <token>`. No manual token
  refresh or shell commands needed.
- **Compat**: `"supportsDeveloperRole": false` is required because Kimi K2 Thinking
  rejects the `developer` message role that Pi sends by default.
- **`models.json` hot-reloads**: Pi reads the file each time you open `/model`, so edits
  take effect without restarting.

#### Auth token injection for vertex-maas

When using `pi-cli` with a `vertex`-prefixed provider, the harness resolves a GCP access
token **once per batch** (not per item) via `google.auth` auto-refresh credentials
(`metaproc[gcp]` extra).

`resolve_gcp_token()` delegates to `cloud.gcp.resolve_token.resolve_gcp_token()`, which
uses `google.auth.default()` with automatic token refresh.
Tokens auto-refresh before expiry — no TTL guessing, no subprocess shell-outs.
The token is injected into the adapter’s `api_key` config field, which becomes part of
each item’s `runtime_config`. Raises on failure — there is no degraded fallback path.

### 3.13 CLI Commands

| Command | Purpose |
| --- | --- |
| `gcp status` | Show orchestrator + worker job states |
| `gcp scale` | Change desired worker topology for an active cloud fan-out step |
| `gcp logs` | Stream logs from Cloud Logging |
| `gcp cancel` | Cancel running/queued Batch jobs |
| `gcp runs` | List all active metaproc runs |
| `gcp self-install` | Install metaproc on a GCP VM |
| `gcp resources` | Show GCP resource usage |
| `gcp filestore` | Manage Filestore NFS |
| `gcp archive` | Archive completed runs |
| `gcp remote` | Run metaproc commands on gateway host via SSH/IAP |
| `gcp remote-run` | Launch `run-process` in a tmux session on a remote GCE host |
| `gcp cleanup` | Clean up cloud resources |

### 3.14 Module Summary

| Module | Role |
| --- | --- |
| `cloud/gcp/batch_backend.py` | GCPBatchConfig and shared Batch API utilities |
| `cloud/gcp/worker_dispatch.py` | Multi-VM item partitioning and dispatch |
| `cloud/gcp/worker_entrypoint.py` | Unified container entrypoint for workers |
| `cloud/gcp/orchestrator_dispatch.py` | Submit orchestrator as GCP Batch job |
| `cloud/gcp/orchestrator_entrypoint.py` | Orchestrator container entrypoint |
| `cloud/gcp/container_bootstrap.py` | Shared git clone + env setup |
| `cloud/gcp/resolve_token.py` | GCP access token via google.auth |
| `cloud/gcp/gcp_credentials.py` | Service account credential management |
| `cloud/gcp/dispatch_artifacts.py` | Wheel build + workspace tarball + GCS upload helpers |
| `cloud/gcp/gcp_run_dispatch.py` | `metaproc gcp run` Job builder + Batch submit |
| `cloud/gcp/gcp_run_entrypoint.py` | `metaproc gcp run` task entrypoint (wheel install + execvp) |
| `cloud/gcp/gcp_run_logs.py` | Blocking-mode log tail + exit-code propagation |
| `cloud/gcp/billing.py` | Approximate billable hours from machine type + worker runtime spans |
| `cloud/gcp/prefect_flow.py` | Prefect `@flow` wrapper for `run-process --backend gcp-worker` (requires `prefect` extra) |

### 3.15 `metaproc gcp run` — Arbitrary Command Dispatch

A complement to the orchestrator/worker model in §3.3-§3.6 for running **arbitrary
one-off commands** on GCP Batch with the dispatcher’s current metaproc + repo state.
Used for detached command fan-out and ad-hoc debug probes.

**Pipeline.** `commands/gcp_run.py` parses CLI flags, builds a `GCPBatchConfig`, ships
artifacts (`build_wheel` + `package_workspace` under `dispatch_artifacts.py`, gated by
`--no-wheel` / `--no-workspace`), and calls `gcp_run_dispatch.dispatch_gcp_run`, which
assembles a single Batch task whose container entrypoint is
`python -m metaproc.cloud.gcp.gcp_run_entrypoint`. The entrypoint `uv tool install`’s
the staged wheel (if `METAPROC_WHEEL_GCS` is set), extracts the workspace tarball over
the cloned repo (if `METAPROC_WORKSPACE_GCS` is set), runs adapter `bootstrap()` hooks
per §2.8, and `execvp`’s the user command from `METAPROC_GCP_RUN_CMD` (JSON-encoded
argv).

**Blocking semantics.** Default mode tails Cloud Logging (filter `labels.job_uid=<uid>`)
prefixed `[gcp-run]` until the job hits a terminal state, then exits with
`SUCCEEDED → 0`, `CANCELLED` / `DELETION_IN_PROGRESS → 130`, otherwise `1`. `--detach`
skips the tail entirely and prints job name + console URL.

**Why a separate primitive.** Worker dispatch (§3.3) is shaped around per-item
partitioning and resume contracts; that machinery is overkill for “run `echo` once” or
“run a package-specific analyzer against a fixed input file.”
The two paths share `batch_backend.py`, `container_bootstrap.py`, the `GCP_SECRET_REFS`
registry, and the Filestore mount script — but `metaproc gcp run` carries no
orchestrator lease, no claim registry, no per-item dispatch manifest.

**Wheel / workspace overrides apply to both paths.** `METAPROC_WHEEL_GCS` and
`METAPROC_WORKSPACE_GCS` are not specific to `metaproc gcp run`. Standard
`run-process --cloud` dispatch forwards both env vars into the worker and orchestrator
Batch envs, and `bootstrap_container()` honors them the same way `bootstrap_gcp_run()`
does (wheel force-reinstalls into `/opt/venv`; workspace tarball extracts into
`/workspace` and reinstalls `example_plugin/` from it, replacing the sparse clone).
This is the supported way to ship a current-branch `metaproc/` fix to workers without an
agent-image rebuild.

See [`cloud-dispatch.runbook.md`](../runbooks/cloud-dispatch.runbook.md) §4b for
operator recipes and this document for the full design.

## 4. AWS Implementation

> **Not yet implemented.** AWS support would follow the same two-tier model (section
> 2.1) with provider-appropriate services: AWS Batch for compute, EFS for shared NFS
> state, Secrets Manager for credential injection, CloudWatch for log streaming.
> The CLI subcommand would be `metaproc aws` with provider-specific operational
> commands. Each worker VM would run `run-parallel --backend local` internally, identical
> to the GCP implementation.

## Future Considerations

### Open Questions

- Should `SecretRefSet` provider-ref aggregation be lazy (current) or eagerly validated
  at dispatch time? The current design silently skips unresolvable provider refs, which
  could mask a misconfigured credential until the adapter fails at runtime.
- The Prefect flow (`prefect_flow.py`) is present in the codebase but not integrated
  into the main dispatch path.
  Its role relative to GCP Batch direct dispatch is [unverified]; clarify whether it is
  a live alternative or a deprecated experiment.
- `billing.py` approximates billable hours from machine type + runtime spans but cannot
  reconcile against actual GCP invoices.
  Is the approximation accurate enough for attribution, or should it be replaced with
  Billing API queries?

### Potential Improvements

- Auth-pool dispatch passthrough adds ~6 fields to both `WorkerDispatchConfig` and
  `OrchestratorDispatchConfig`. A shared `AuthPoolFlags` payload (already used in
  `WorkerDispatchConfig`) could replace the individual fields in
  `OrchestratorDispatchConfig` for consistency.
- The container bootstrap module docstring lists 6 steps but the code performs 7
  (adapter `bootstrap(home)` hooks are invoked by the entrypoint callers, not by
  `bootstrap_container()` itself).
  Aligning the module docstring with the actual flow would reduce confusion.
- `run_cloud_preflight()` validates env-var presence but does not probe GCP API
  reachability (e.g., can the Batch API be called?
  Is the Filestore server resolvable?). Adding a lightweight API probe could catch
  misconfigured networks before job submission.

## Revision History

### rev6 (2026-08-02)

Exact typed run identity:

- Documented readable `metaproc-run-id` versus collision-resistant `metaproc-run-key`
  labels on worker and orchestrator jobs.
- Updated monitoring to describe exact lookup, hash-verified mixed-generation jobs, the
  fully legacy fallback, and exact identity recovery/grouping in `gcp runs`.
- Documented canonical local status identity resolution for process-subdirectory
  layouts.
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
- Updated `GCP_SECRET_REFS` to reflect `SecretRefSet` refactor
  (`dispatch/secret_refs.py`) and added `CODEX_CREDS_JSON` row.
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

- **§2.8 Container Bootstrap Contract**: added step 7 — adapter `bootstrap(home)` hook
  for credential files not safe to keep as env vars for the job lifetime.
- **§3.1 Infrastructure Components**: Secrets bullet now cites the Claude Code CLI
  Personal-Plan OAuth blob alongside `GH_TOKEN`.
- **§3.2 Container Bootstrap**: documents the adapter-bootstrap invocation (Claude Code
  CLI writes `~/.claude/.credentials.json` from `CLAUDE_CODE_CREDS_JSON` and unsets the
  env var).
- **§3.10 Secret Manager Integration**: generalized from GH_TOKEN-only to the
  `GCP_SECRET_REFS` registry pattern; added the two-hop Keychain → Secret Manager →
  adapter-bootstrap flow for the Claude credential; cross-links to credential-setup.md
  and cloud-dispatch.runbook.md.
- **§3.11 GCP Configuration Environment Variables**: added
  `METAPROC_GCP_SECRET_CLAUDE_CREDS`.

### rev4 (2026-04-19)

`metaproc gcp run` arbitrary-command dispatch primitive:

- **§3.14 Module Summary**: added `dispatch_artifacts.py`, `gcp_run_dispatch.py`,
  `gcp_run_entrypoint.py`, `gcp_run_logs.py`.
- **§3.15 `metaproc gcp run`**: new section documenting the primitive alongside the
  orchestrator/worker model — pipeline, blocking semantics, and why it’s a separate
  primitive (no lease, no claims, no dispatch manifest).

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
