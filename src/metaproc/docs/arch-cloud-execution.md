---
title: "Architecture: Cloud Execution"
description: "Cloud-execution subsystem: GCP Batch dispatch, worker entrypoints, container lifecycle, cross-host coordination."
author: metaproc team
status: Approved
---
# Architecture: Cloud Execution

**Date:** 2026-04-12 (last updated 2026-08-27) **Status:** Approved

For the overall metaproc framework design, see [metaproc-design.md](metaproc-design.md);
for the run pool process management subsystem, see [arch-runpool.md](arch-runpool.md).

## 1. Background and Requirements

### 1.1 Problem

Local execution has inherent scaling limits: a single machine constrains concurrent
agent invocations by CPU, memory, and network bandwidth.
Running more than 500 fan-out items locally takes hours even with aggressive
concurrency, and memory pressure forces the adaptive pool to shed slots.

Cloud execution solves this by distributing fan-out items across multiple VMs, each
running its own run pool.
The challenge is doing this without introducing a cloud-specific execution model: the
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
Workers default to preemptible or Spot VMs for cost efficiency; killed items are
retryable on resume.

### 2.2 Execution Chain by Topology

The same process spec supports local and full-cloud execution.
In the full-cloud path, the outer command submits the orchestrator itself to Batch; a
laptop never remains in the orchestration or storage path.

| Layer | Local | Full Cloud (`--cloud`) |
| --- | --- | --- |
| Entry | `metaproc run-process` on operator host | `metaproc run-process --cloud` on operator host |
| Orchestrator | `run-process` on operator host | `run-process` on orchestrator VM |
| Code steps | Run locally | Run on orchestrator VM |
| Fan-out dispatch | `run-parallel` locally | `dispatch_to_workers()` -> N worker VMs |
| Item execution | RunPool with LocalBackend locally | RunPool with LocalBackend on each worker VM |
| Adapter subprocess | `pi`/`claude`/`gemini` locally | `pi`/`claude`/`gemini` on worker VM |
| Authoritative live/restart state | Local disk | Filestore NFS |

Filestore is authoritative only while a cloud run is executing or remains restartable.
Metaproc does not claim terminal NFS durability.
Before reclaiming that scratch storage, the downstream consumer must publish the
accepted terminal run tree to its registered durable object-store contract and verify
that publication under its own policy.

#### 2.2.1 Target Placement Model

Orchestrator placement and worker placement are separate topology axes.
The implemented CLI still exposes them indirectly through `--cloud` and
`--backend gcp-worker`; a future atomic CLI change will expose them as `--orchestrator`
and `--worker`. The target vocabulary is:

```text
metaproc run-process <spec> \
  --orchestrator local|gcp \
  --worker colocated|gcp
```

`colocated` means fan-out executes in the orchestrator’s environment.
It does not mean “the operator laptop” when the orchestrator is in GCP.

| Orchestrator | Worker | Meaning | Availability |
| --- | --- | --- | --- |
| `local` | `colocated` | Ordinary local process | Current |
| `gcp` | `gcp` | Batch orchestrator and distributed Batch workers | Current through `--backend gcp-worker --cloud` |
| `gcp` | `colocated` | One cloud execution environment owns orchestration and work | Planned |
| `local` | `gcp` | Local interactive orchestrator with GCP workers | Planned; requires an explicit state transport |

The first placement implementation has one run-wide worker placement and resource
profile. That placement may be `colocated` or `gcp`, but every worker uses the same
provider, image, machine class, identity, secret policy, and network policy.
The pool may still span multiple Batch VMs; run-wide means those workers share one
placement and profile, not that they share the orchestrator host.
Per-step placement overrides and adapter- or harness-specific pools are later
extensions, not requirements for the first CLI change.

The CLI resolver must produce one immutable execution-topology value before planning or
dispatch. The process engine consumes that resolved value rather than testing raw CLI
strings or provider environment variables.
That value contains the orchestrator placement, the run-wide worker placement and
resource profile, and a state-transport strategy.
Provider dispatchers translate placements into provider jobs; the process graph does not
contain GCP scheduling branches.

Same-locus topologies may select the existing filesystem transport.
A split-locus topology is valid only after a registered transport implements immutable
dispatch inputs, leases, events, claims, results, failures, cancellation, and resume.
It must fail closed before dispatch when no such transport is available; SSHFS, a
laptop-mounted Filestore, and path-identity aliases are not transports.
Later per-step worker profiles can override the run-wide default in the resolved
topology without changing the two public placement axes.

SSH remains a valid control transport for a future persistent-host placement, but it is
not a pipeline state transport.
The open-source MetaBrowser project owns the maintained
[SSH command, IAP, tunnel, and disconnect-watchdog utilities](https://github.com/jlevy/metabrowser/blob/main/src/metabrowser/cli/ssh_utils.py)
and their
[remote-command integration](https://github.com/jlevy/metabrowser/blob/main/src/metabrowser/cli/remote.py).
If Metaproc gains an SSH-backed placement with a real consumer, reuse or extract that
implementation rather than restoring an unused Metaproc copy.

### 2.3 Fan-Out Backend Dispatch

Fan-out steps in `run-process` dispatch through a backend selected via `--backend`:

| Backend | Flag | Mechanism |
| --- | --- | --- |
| `local` | `--backend local` (default) | `RunPool` subprocess pool via `run-parallel` |
| `gcp-worker` | `--backend gcp-worker --cloud` | Submit the orchestrator, which partitions items across N worker VMs via GCP Batch |

**Note on backend abstraction:** `local` is a registered `LaunchBackend` implementation
(see section 2.5) in the backend registry (`runpool/registry.py`). Cloud worker backends
are different: they are multi-VM dispatch modes handled directly in `run-process`, not
`LaunchBackend` implementations.
A cloud worker backend partitions items across N VMs, each of which runs
`run-parallel --backend local` internally.
The bare `--backend gcp-worker` form is reserved for the inner Batch orchestrator leg
only. `orchestrator_dispatch.py` sets `METAPROC_GCP_ORCHESTRATOR=1`; the inner process
also accepts the GCP-provided `BATCH_TASK_INDEX` as a fallback runtime signal.
The explicit dispatcher marker is the primary admission contract, so a missing provider
variable cannot reject a correctly dispatched orchestrator.

If a second cloud provider were added, a new worker dispatch implementation would
register alongside `gcp-worker` in the `run-process` dispatch logic.

### 2.4 Filesystem-First Resume Contract

Authoritative live and restart state lives only on the run filesystem: local disk for
full-local runs and shared NFS for full-cloud runs.
Full per-artifact schemas and lifecycles live in
[artifact-catalog.md](artifact-catalog.md); this section covers the dispatch-relevant
subset.

**`run-config.yaml`** (`{run_dir}/.state/run-config.yaml`): written at run creation time
with the process name, run ID, resolved variables, creation-time backend and variant,
git SHA, and timestamp.
On resume, the process identity, run directory, and resolved variables must match.
The two canonical cloud Filestore mount roots normalize to one identity; workstation
paths do not. No other variable changes are accepted.
Cross-topology resume (for example, hybrid to full cloud) remains allowed because the
backend is not part of resume identity and both topologies share the authoritative
filesystem. Authentication and concurrency changes remain explicit timeline events.

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
Adding a new local backend means implementing the five-method protocol and registering
it; no changes to `engine/`, `runpool/`, or `models/` are required.

### 2.6 Provider Naming and Extensibility

The cloud layer uses provider-specific names rather than a generic `cloud` abstraction.

**CLI subcommand:** `metaproc gcp` (not `metaproc cloud`). The commands under a provider
subgroup are inherently provider-API-specific: they query provider batch APIs, stream
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
metaproc; the framework provides CLI commands that can be run anywhere.

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

1. Resolve dispatched secret-store references under the workload identity before logging
   runtime context or reading bootstrap inputs.
2. Configure git identity and credential helper.
3. Use the bundled repo content from the image when possible, or sparse-clone the
   requested branch when a runtime branch override is needed.
4. Install the domain package(s) needed for plugin discovery.
5. Bootstrap any opt-in domain tooling required by the process.
6. Ensure the runs directory exists on the shared filesystem.
7. Write any adapter-specific configuration files from environment variables.
8. Invoke each adapter’s `bootstrap(home)` hook so adapters can materialize credential
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
- **Storage:** Filestore NFS for shared live execution and restart state across all VMs;
  accepted terminal trees move to the downstream consumer’s registered durable
  object-store contract before scratch reclamation.
- **Secrets:** Secret Manager for credential injection (e.g., `GH_TOKEN`, Claude Code
  CLI Personal-Plan OAuth blob).
  See §3.10.
- **Logging:** Cloud Logging for centralized log streaming.
- **Container:** Docker images with pre-installed metaproc and agent CLIs.

### 3.2 Container Bootstrap (`container_bootstrap.py`)

Shared by worker and orchestrator entrypoints via
`bootstrap_container() -> BootstrapResult`:

1. Before `bootstrap_container()`, call `hydrate_secret_env()` to resolve the
   dispatcher’s Secret Manager references atomically under the attached Batch service
   account. No resource-context log or bootstrap action runs if hydration fails.
2. Read `GH_TOKEN` once, remove it from the environment, and expose it only through a
   temporary askpass helper when a sparse clone needs authentication.
3. Install a current-branch metaproc wheel from `METAPROC_WHEEL_GCS` when set with its
   required `METAPROC_WHEEL_SHA256`, overriding any image-baked metaproc.
4. Acquire the consumer workspace: a `METAPROC_WORKSPACE_GCS` tarball when set, verified
   against its required `METAPROC_WORKSPACE_SHA256`; otherwise a sparse clone of
   `METAPROC_RUN_BRANCH` and `METAPROC_REPO_URL`, falling back to the workspace bundled
   into the image.
5. Editable-install the workspace packages named in the repo-sync payload so consumer
   plugin entry points resolve inside the container.
6. Run each `metaproc.container_bootstrap` entry-point hook so downstream images can
   bootstrap their own tooling.
7. Ensure `RUNS_DIR` exists.
8. Write `~/.pi/agent/models.json` from `METAPROC_PI_MODELS_JSON` if set.
9. Back in the worker and orchestrator entrypoints, invoke each registered adapter’s
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

1. Hydrate Secret Manager references as described in §3.10. Fail before resource logging
   or bootstrap if any binding is invalid or inaccessible.
2. Read env vars (`METAPROC_WORKER_ITEMS`, `METAPROC_PROCESS_SPEC` (with
   `METAPROC_PROCESS_DIR` as legacy fallback), `METAPROC_STEP`, etc.). Auth-pool
   dispatch env vars (`METAPROC_AUTH_ACCOUNT`, `METAPROC_AUTH_BACKEND`,
   `METAPROC_AUTH_FALLBACK_POLICY`, `METAPROC_AUTH_INCLUDE_LABELS`,
   `METAPROC_AUTH_EXCLUDE_LABELS`) are forwarded as `--auth-*` flags when present;
   without them, the worker falls back to single-credential bootstrap.
3. Call `bootstrap_container()`.
4. Build and run:
   `python -m metaproc run-parallel <process_spec> --step <step> --items <items> --backend local [--auth-* flags]`.
5. Exit with `run-parallel`’s exit code.
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
8h), `poll_interval`, and one `auth_flags` (`AuthPoolFlags`) payload for the complete
authentication-pool transport cohort.

### 3.6 Orchestrator Entrypoint (`orchestrator_entrypoint.py`)

1. Hydrate Secret Manager references as described in §3.10. Fail before resource logging
   or bootstrap if any binding is invalid or inaccessible.
2. Read orchestrator env vars.
3. Call `bootstrap_container()`.
4. Let the process DAG materialize any roster/run inputs on NFS via ordinary in-DAG code
   steps.
5. Build and run:
   `python -m metaproc run-process <process_dir> --backend gcp-worker [all forwarded flags]`.
6. Forward the dispatcher-owned `METAPROC_GCP_ORCHESTRATOR=1` admission marker to the
   inner process.
7. Does **not** pass `--cloud` to avoid infinite recursion.
8. Exit with `run-process`’s exit code.

### 3.7 GCP Batch Shared Utilities (`batch_backend.py`)

`batch_backend.py` provides shared configuration and helpers used by both
`worker_dispatch.py` and `orchestrator_dispatch.py` for submitting GCP Batch jobs.

`GCPBatchConfig` (frozen dataclass): `project`, `region`, `container_image`,
`machine_type`, `spot`, `boot_disk_gb`, `max_run_duration_s`, `service_account_email`,
`network`/`subnetwork`, `labels`, `runs_dir`,
`filestore_server`/`filestore_share`/`filestore_mount_path`, `queue_timeout_s`.

Utility functions: `sanitize_label()` (GCP-safe resource names and readable labels),
`run_identity_label()` / `run_identity_labels()` (fixed-width exact run locator plus
readable compatibility label), `build_pi_models_json()` (rewrite pi CLI config for
cloud), `infer_compute_resource()` / `resolve_compute_resource()` /
`build_compute_resource()` (right-size container cgroup from VM machine type; see
below), `is_transient_api_error()` (retry classification).

#### Container compute_resource

GCP Batch defaults an unset `TaskSpec.compute_resource` to **2000 cpu_milli / 2000
memory_mib** regardless of the underlying VM. On `n2-highmem-8` (8 vCPU / 64 GiB) that
means the container’s cgroup gets ~3% of the host’s RAM, and long-running agent
workloads get OOM-killed by the cgroup well before the host runs out of anything.
The Phase 1c Qwen3 235B baseline hit this pattern explicitly: `metaproc.runpool`
correctly read `memory_ceiling=1` from the 2 GiB cgroup and cut concurrency, then
SIGKILL fired anyway.

`build_compute_resource(machine_type)` derives the right cgroup from the VM shape via
`infer_compute_resource(machine_type)`:

- Parses `<family>-<class>-<N>` (e.g. `n2-highmem-8`) and `custom-CPU-MEM`.
- Class ratios (`standard` 4 GiB/vCPU, `highmem` 8, `highcpu` 1, `megamem` 13, and
  `ultramem` 28) match GCE published shapes.
- Reserves `HOST_CPU_RESERVE_MILLI=500` and `HOST_MEMORY_RESERVE_MIB=1024` for the Batch
  Batch agent, Docker daemon, and host OS; the rest goes to the container.
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
- **`gcp resources` / `gcp filestore` / `gcp cleanup`**: operator tools for cloud
  inventory, Filestore utilization, and terminal-job cleanup.
- **NFS progress polling**: during worker dispatch, the orchestrator reads
  `runpool-status.yaml` from NFS to report live progress (completed/failed/active).
- **NFS error extraction**: on worker failure, reads `runpool-status.yaml` for failure
  counts and per-process kill reasons.
- Filesystem-oriented commands such as `status`, `pulse`, and `pool status` require an
  explicit, locally visible run-directory path.
  They do not route through a persistent gateway VM.

### 3.9 GCP Mount Path

Batch worker and orchestrator VMs mount the Filestore NFS share at `/mnt/filestore` by
default. `RUNS_DIR` resolves to `<mount_path>/runs`, not the bare share root, so run
trees live at `/mnt/filestore/runs/{run_id}/`. The mount path is a container-level
Volume mount point set via the Batch API Volume spec, not subject to COS host-level path
restrictions.

### 3.10 Secret Manager Integration

Every credential delivered to a Batch job comes from Secret Manager.
Dispatch never puts the plaintext value in the Batch job spec, and it deliberately does
not use Batch `Environment.secret_variables`: the Batch agent expands those values into
its generated `docker run --env` command, which can expose them in agent logs.

The current contract is reference-only until the container starts:

1. `SecretRefSet.all_known()` composes static bindings with provider bindings from
   `metaproc.config.providers.gcp_secret_refs()` and refuses any ambient plaintext
   credential that lacks its corresponding Secret Manager reference.
2. Dispatch serializes only `{target_env: version_resource}` into
   `METAPROC_GCP_SECRET_REFS_JSON`. The job spec and Batch agent therefore see resource
   names, never secret values.
3. The Batch VM runs under the explicit `METAPROC_GCP_SERVICE_ACCOUNT` identity.
4. `hydrate_secret_env()` validates the complete mapping, fetches every version through
   the Secret Manager API, and mutates the process environment only after all fetches
   succeed. It refuses pre-existing target variables and never logs values.
5. The generic-run, orchestrator, and worker entrypoints all hydrate before bootstrap;
   the orchestrator retains the operator-side resource refs so it can bind its workers
   through the same contract.

Static refs (as of 2026-05-23):

| Plaintext env | Secret Manager env | Purpose |
| --- | --- | --- |
| `GH_TOKEN` | `METAPROC_GCP_SECRET_GH_TOKEN` | Private-repo access from Batch VMs |
| `CLAUDE_CODE_CREDS_JSON` | `METAPROC_GCP_SECRET_CLAUDE_CREDS` | Claude Code CLI Personal-Plan OAuth blob |
| `CODEX_CREDS_JSON` | `METAPROC_GCP_SECRET_CODEX_CREDS` | Codex CLI ChatGPT-OAuth blob |

Provider-specific refs (pi-cli API keys, etc.)
are added dynamically via `gcp_secret_refs()` in `metaproc.config.providers`; adding a
new provider credential there is sufficient, no edits to dispatch wiring needed.

`SecretRef.resolve()` enforces the uniform anti-leakage policy; adding a provider
credential to the provider registry automatically includes it in all three dispatch
paths.

The Claude Code CLI OAuth blob is a two-hop credential: the operator pushes
`~/.claude/.credentials.json` (read from macOS Keychain) to Secret Manager via
`metaproc claude-auth push`; dispatch binds `METAPROC_GCP_SECRET_CLAUDE_CREDS` →
`CLAUDE_CODE_CREDS_JSON` through the reference-only hydration contract; the
`ClaudeCodeCliAdapter.bootstrap(home)` hook (invoked by §2.8 / §3.2) materializes the
credential file on the worker and unsets the env var.
See [credential-setup.runbook.md](credential-setup.runbook.md) for the operator setup
flow and [cloud-dispatch.runbook.md](cloud-dispatch.runbook.md) → *GCP Batch (Personal
Plan)* for the end-to-end dispatch recipe.

### 3.11 GCP Configuration Environment Variables

All GCP infrastructure parameters are configurable via env vars:

- `METAPROC_GCP_PROJECT`: GCP project ID
- `METAPROC_GCP_FILESTORE_SERVER`: Filestore IP or hostname
- `METAPROC_GCP_FILESTORE_SHARE`: Filestore share name
- `METAPROC_GCP_FILESTORE_MOUNT_PATH`: NFS mount path (default `/mnt/filestore`)
- `METAPROC_GCP_SECRET_GH_TOKEN`: Secret Manager resource name for GH_TOKEN
- `METAPROC_GCP_SECRET_CLAUDE_CREDS`: Secret Manager resource name for the Claude Code
  CLI Personal-Plan OAuth blob (required when dispatching `variant=claude-code-cli` on
  Batch with the subscription credential)
- `METAPROC_GCP_SECRET_CODEX_CREDS`: Secret Manager resource name for the Codex CLI
  ChatGPT-OAuth blob
- `METAPROC_RUN_BRANCH`: git branch for container bootstrap
- `METAPROC_REPO_URL`: repo URL for container bootstrap

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
Tokens auto-refresh before expiry, with no TTL guessing or subprocess shell-outs.
The token is injected into the adapter’s `api_key` config field, which becomes part of
each item’s `runtime_config`. Failure raises an exception; there is no degraded fallback
path.

### 3.13 CLI Commands

| Command | Purpose |
| --- | --- |
| `gcp status` | Show orchestrator and worker job states |
| `gcp scale` | Change desired worker topology for an active cloud fan-out step |
| `gcp logs` | Stream logs from Cloud Logging |
| `gcp cancel` | Cancel running/queued Batch jobs |
| `gcp runs` | List all active metaproc runs |
| `gcp run` | Run one lower-level command in a single Batch task |
| `gcp resources` | Show GCP resource usage |
| `gcp filestore` | Manage Filestore NFS |
| `gcp cleanup` | Clean up cloud resources |

### 3.14 Module Summary

| Module | Role |
| --- | --- |
| `cloud/gcp/batch_backend.py` | GCPBatchConfig and shared Batch API utilities |
| `cloud/gcp/worker_dispatch.py` | Multi-VM item partitioning and dispatch |
| `cloud/gcp/worker_entrypoint.py` | Unified container entrypoint for workers |
| `cloud/gcp/orchestrator_dispatch.py` | Submit orchestrator as GCP Batch job |
| `cloud/gcp/orchestrator_entrypoint.py` | Orchestrator container entrypoint |
| `cloud/gcp/container_bootstrap.py` | Shared source resolution and environment setup |
| `cloud/gcp/resolve_token.py` | GCP access token via google.auth |
| `cloud/gcp/gcp_credentials.py` | Service account credential management |
| `cloud/gcp/dispatch_artifacts.py` | Wheel build, workspace archive, and GCS upload helpers |
| `cloud/gcp/gcp_run_dispatch.py` | `metaproc gcp run` job builder and Batch submission |
| `cloud/gcp/gcp_run_entrypoint.py` | `metaproc gcp run` task entrypoint for wheel installation and command execution |
| `cloud/gcp/gcp_run_logs.py` | Blocking log tail and exit-code propagation |
| `cloud/gcp/billing.py` | Approximate billable hours from machine type and worker runtime spans |

### 3.15 `metaproc gcp run`: Arbitrary Command Dispatch

This is the lower-level primitive for running **one arbitrary command in one Batch
task** with the dispatcher’s current Metaproc and repository state.
Appropriate uses include probes, diagnostics, terminal publication, and an application
that already owns its outer orchestration.
An application process should normally use `run-process --cloud`, which preserves the
framework’s graph, lease, claim, retry, resume, and worker-fan-out contracts.
Scripts must not construct a process by chaining `gcp run` calls.

**Pipeline.** `commands/gcp_run.py` parses CLI flags, builds a `GCPBatchConfig`, ships
artifacts (`build_wheel` and `package_workspace` under `dispatch_artifacts.py`, gated by
`--no-wheel` / `--no-workspace`), and calls `gcp_run_dispatch.dispatch_gcp_run`, which
assembles a single Batch task whose container entrypoint is
`python -m metaproc.cloud.gcp.gcp_run_entrypoint`. The entrypoint verifies the staged
wheel against `METAPROC_WHEEL_SHA256` and force-reinstalls it into `/opt/venv` with
`uv pip install` when `METAPROC_WHEEL_GCS` is set.
It verifies and safely extracts the workspace archive into `/workspace` when the
corresponding URI and digest pair is set.
Each repeated `--workspace-package` path is then installed editable from that archive,
and nested `uv` commands stay on the baked `/opt/venv` without re-resolving the shipped
workspace. A run with no workspace archive also pins nested `uv` commands to the baked
environment, because consumer source must already be present in the image and no project
lock is available. A shipped Metaproc wheel may still replace its image-baked version.
A full shipped workspace without editable package installation retains ordinary uv
project resolution.
The entrypoint runs adapter `bootstrap()` hooks per §2.8 and executes
the JSON-encoded argv from `METAPROC_GCP_RUN_CMD` with `execvp`.

The default workspace archive excludes both the historical top-level `metaproc/` source
layout and a `vendor/metaproc` gitlink.
The wheel is the sole Metaproc source shipped by this path, so an initialized vendored
checkout cannot be recursively copied into the consumer archive.

**Blocking semantics.** Default mode tails Cloud Logging (filter `labels.job_uid=<uid>`)
prefixed `[gcp-run]` until the job hits a terminal state, then exits with
`SUCCEEDED → 0`, `CANCELLED` / `DELETION_IN_PROGRESS → 130`, otherwise `1`. `--detach`
skips the tail entirely and prints the job name and console URL.

**Why a separate primitive.** Worker dispatch (§3.3) is shaped around per-item
partitioning and resume contracts; that machinery is overkill for “run `echo` once” or
“run a package-specific analyzer against a fixed input file.”
The two paths share `batch_backend.py`, `container_bootstrap.py`, `SecretRefSet`,
`secret_hydration.py`, and the Filestore mount script, but `metaproc gcp run` carries no
orchestrator lease, no claim registry, no per-item dispatch manifest.
That absence is its boundary, not an invitation to grow a second orchestration layer.

**Wheel and workspace overrides apply to both paths.** The URI variables and their
required `METAPROC_WHEEL_SHA256` and `METAPROC_WORKSPACE_SHA256` digests are not
specific to `metaproc gcp run`. Standard `run-process --cloud` dispatch forwards all
four values into the worker and orchestrator Batch environments.
`bootstrap_container()` verifies and installs the wheel into `/opt/venv`; a verified
workspace archive replaces the repository clone, and the explicitly configured workspace
packages are installed from that archive.
This can ship code imported after bootstrap without an agent-image rebuild.
It cannot replace the generic-run, orchestrator, or worker entrypoint code already
imported by the image’s initial Python process.
Changes to those entrypoints, secret hydration, or pre-wheel bootstrap require a
candidate image rebuild.

See
[Dispatch an Arbitrary Command](cloud-dispatch.runbook.md#5-dispatch-an-arbitrary-command)
for operator recipes and this document for the full design.

## 4. AWS Implementation

> **Not yet implemented.** AWS support would follow the same two-tier model (section
> 2.1) with provider-appropriate services: AWS Batch for compute, EFS for shared NFS
> state, Secrets Manager for credential injection, CloudWatch for log streaming.
> The CLI subcommand would be `metaproc aws` with provider-specific operational
> commands. Each worker VM would run `run-parallel --backend local` internally, identical
> to the GCP implementation.
