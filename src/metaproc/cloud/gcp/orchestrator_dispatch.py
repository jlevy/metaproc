"""Orchestrator dispatch — submit the DAG orchestrator as a GCP Batch job.

Submits a single GCP Batch job running ``orchestrator_entrypoint.py``,
which clones the repo and executes ``metaproc run-process`` with all
forwarded flags.  Polls until the orchestrator job completes.

Called by ``run-process --cloud`` to move the entire DAG execution to
a GCP VM with Filestore NFS mounted.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets as _secrets
import time
from dataclasses import dataclass

from metaproc.cloud.gcp.batch_backend import (
    GCP_SECRET_REFS,
    GCPBatchConfig,
    apply_container_runs_dir,
    build_batch_runnables,
    build_compute_resource,
    build_job_id,
    build_pi_models_json,
    get_container_runs_dir,
    get_job_with_retry,
    resolve_gcp_secret_ref,
    sanitize_label,
)
from metaproc.config.env_vars import MetaprocEnv
from metaproc.dispatch.auth_pool_flags import AuthPoolFlags
from metaproc.dispatch.orchestrator_payload import OrchestratorDispatchPayload
from metaproc.dispatch.repo_sync_payload import RepoSyncPayload
from metaproc.plugins.discovery import get_bootstrap_env_vars

log = logging.getLogger(__name__)

# Default orchestrator VM sizing.
DEFAULT_ORCHESTRATOR_MACHINE_TYPE = "n2-standard-4"
DEFAULT_MAX_DURATION_S = 8 * 3600  # 8 hours


@dataclass(frozen=True)
class OrchestratorDispatchConfig:
    """Parameters for submitting the orchestrator Batch job."""

    gcp: GCPBatchConfig
    process_spec_rel: str
    variables: dict[str, str]
    num_workers: int = 1
    worker_machine_type: str = ""
    max_concurrency: int | None = None
    initial_concurrency: int | None = None
    spot_workers: bool = True
    variant: str = ""
    adapter_config: dict[str, str] | None = None
    skip_steps: list[str] | None = None
    from_step: str = ""
    only_step: str = ""
    force: bool = False
    continue_on_error: bool = False
    orchestrator_machine_type: str = DEFAULT_ORCHESTRATOR_MACHINE_TYPE
    max_duration_s: int = DEFAULT_MAX_DURATION_S
    poll_interval: int = 60
    # Auth-pool dispatch passthrough. Mirrors `run-process --auth-*` flags
    # so cloud and local execution paths offer the same surface.
    auth_account: str | None = None
    auth_backend: str | None = None
    auth_fallback_policy: str = "none"
    auth_include_labels: tuple[str, ...] = ()
    auth_exclude_labels: tuple[str, ...] = ()
    auth_cross_quota_group: bool = True


@dataclass
class OrchestratorResult:
    """Result from the orchestrator Batch job."""

    job_name: str
    job_id: str
    state: str
    exit_code: int


async def dispatch_orchestrator(
    config: OrchestratorDispatchConfig,
) -> OrchestratorResult:
    """Submit orchestrator Batch job and poll until completion."""
    from google.cloud import batch_v1  # noqa: PLC0415 -- optional [gcp-batch] dependency
    from google.cloud.batch_v1.types import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
        AllocationPolicy,
        Job,
        JobStatus,
        LogsPolicy,
        TaskGroup,
        TaskSpec,
    )

    client = batch_v1.BatchServiceClient()

    runtime_vars = apply_container_runs_dir(config.gcp, config.variables)
    run_id = runtime_vars.get("RUN_ID", "unknown")

    payload = OrchestratorDispatchPayload(
        process_spec=config.process_spec_rel,
        variables=runtime_vars,
        num_workers=config.num_workers,
        machine_type=config.worker_machine_type or "",
        max_concurrency=config.max_concurrency,
        initial_concurrency=config.initial_concurrency,
        spot=config.spot_workers,
        variant=config.variant or "",
        adapter_config=config.adapter_config or {},
        skip_steps=tuple(config.skip_steps or ()),
        from_step=config.from_step,
        only_step=config.only_step,
        force=config.force,
        continue_on_error=config.continue_on_error,
    )
    env_vars: dict[str, str] = dict(payload.to_env_vars())
    env_vars.update(
        {
            "GOOGLE_CLOUD_PROJECT": config.gcp.project,
            "GCLOUD_PROJECT": config.gcp.project,
            "CLOUDSDK_CORE_PROJECT": config.gcp.project,
            # Consumed by pi-cli's `google-vertex` API provider (@google/genai SDK).
            # "global" resolves to https://aiplatform.googleapis.com/ under the SDK
            # (see @google/genai dist/node/index.mjs:12862), which is the only
            # hostname serving Gemini 3.x preview models. See
            # metaproc/docs/runbooks/adapter-compatibility.runbook.md.
            "GOOGLE_CLOUD_LOCATION": "global",
        }
    )

    # Auth-pool passthrough: orchestrator_entrypoint reads these and emits
    # `--auth-*` flags onto the inner `run-process` command. Without them,
    # cloud workers silently fall back to the legacy single-credential path
    # even when operators pass `--auth-account` to the cloud dispatch.
    # AuthPoolFlags centralizes the env-var names + CSV encoding so this
    # site does not hardcode "METAPROC_AUTH_*" strings.
    # OrchestratorDispatchConfig stores `auth_account` / `auth_backend`
    # as `str | None` (None = unset); AuthPoolFlags uses `""` for the
    # absent sentinel. Translate at the boundary so the dispatch config
    # shape does not churn. The legacy "none" string for fallback_policy
    # is also translated to "" here.
    auth_flags = AuthPoolFlags(
        auth_account=config.auth_account or "",
        auth_backend=config.auth_backend or "",
        auth_fallback_policy=(
            config.auth_fallback_policy
            if config.auth_fallback_policy and config.auth_fallback_policy != "none"
            else ""
        ),
        auth_include_labels=tuple(config.auth_include_labels),
        auth_exclude_labels=tuple(config.auth_exclude_labels),
        auth_cross_quota_group=config.auth_cross_quota_group,
    )
    env_vars.update(auth_flags.to_env_vars())

    # Pool owner: GcpSecretManagerBackend names secrets as
    # ``claude-code-auth-<pool_user>-<label>``. The orchestrator's own
    # pre-flight probe and the worker leg both call ``gcp_backend(project,
    # pool_user=...)`` and resolve pool_user via
    # ``METAPROC_AUTH_POOL → USER → "unknown"``. Inside the orchestrator
    # container USER is empty, so the resolution falls through to "unknown"
    # and the SM lookup hits a 404 against ``claude-code-auth-unknown-<label>``.
    # Propagate the locally-resolved pool_user explicitly.
    pool_user = MetaprocEnv.METAPROC_AUTH_POOL.read_str(default="") or MetaprocEnv.USER.read_str(
        default=""
    )
    if pool_user:
        env_vars["METAPROC_AUTH_POOL"] = pool_user

    # Forward GCP config so orchestrator can dispatch workers.
    env_vars["METAPROC_GCP_PROJECT"] = config.gcp.project
    env_vars["METAPROC_GCP_REGION"] = config.gcp.region
    env_vars["METAPROC_GCP_CONTAINER_IMAGE"] = config.gcp.container_image
    if config.gcp.filestore_server:
        env_vars["METAPROC_GCP_FILESTORE_SERVER"] = config.gcp.filestore_server
    if config.gcp.filestore_share:
        env_vars["METAPROC_GCP_FILESTORE_SHARE"] = config.gcp.filestore_share
    if config.gcp.filestore_mount_path:
        env_vars["METAPROC_GCP_FILESTORE_MOUNT_PATH"] = config.gcp.filestore_mount_path
    env_vars["METAPROC_GCP_BOOT_DISK_GB"] = str(config.gcp.boot_disk_gb)
    if config.gcp.service_account_email:
        env_vars["METAPROC_GCP_SERVICE_ACCOUNT"] = config.gcp.service_account_email
    if config.gcp.network:
        env_vars["METAPROC_GCP_NETWORK"] = config.gcp.network
    if config.gcp.subnetwork:
        env_vars["METAPROC_GCP_SUBNETWORK"] = config.gcp.subnetwork

    # Filestore NFS — RUNS_DIR points to <mount_path>/runs so run-id
    # directories live in a runs/ subdirectory, not at the share root.
    # Always set when filestore is configured, even if local RUNS_DIR is empty.
    runs_dir = get_container_runs_dir(config.gcp)
    if runs_dir:
        env_vars["RUNS_DIR"] = runs_dir

    # Propagate orchestrator --max-duration to worker VMs so workers aren't
    # silently capped at the worker_dispatch default (8h). Workers need to
    # finish inside the orchestrator's own deadline anyway, so using the
    # same value is the right ceiling. Operators can still override via
    # METAPROC_GCP_MAX_RUN_DURATION_S in the local env if they really want
    # workers capped lower than the orchestrator.
    if "METAPROC_GCP_MAX_RUN_DURATION_S" not in env_vars:
        env_vars["METAPROC_GCP_MAX_RUN_DURATION_S"] = str(config.max_duration_s)

    env_vars.update(RepoSyncPayload.from_env().to_env_vars())

    # Credentials injected via Secret Manager only — plaintext values would
    # persist in the Batch job spec (visible via `gcloud batch jobs describe`
    # and the API). For each entry we also forward METAPROC_GCP_SECRET_* as a
    # plain env var so the orchestrator can pass it through when dispatching
    # workers. See `GCP_SECRET_REFS` in batch_backend.py for the table.
    secret_env_vars: dict[str, str] = {}
    for plaintext_env, secret_env, description in GCP_SECRET_REFS:
        ref = resolve_gcp_secret_ref(plaintext_env, secret_env, description)
        if ref:
            env_vars[secret_env] = ref
            secret_env_vars[plaintext_env] = ref

    # CLAUDE_CODE_OAUTH_TOKEN for orchestrator-side single-item agent steps
    # (create-ops-review, create-prediction-summary, create-run-overview).
    # These run on the orchestrator container via _execute_agent_step (NOT
    # _execute_fan_out_step → _run_agent_pool), so they don't go through
    # the per-slot pool acquire path that injects the bearer per-item.
    # Without an ambient bearer in the orchestrator's env, claude reports
    # `apiKeySource: "none"` and exits with "Not logged in · Please run
    # /login". Source from the first include label's SM secret (matches
    # the pool's priority order; if alt1 cools, single-item steps degrade
    # but fan-out keeps rotating). Pool user is the same value
    # propagated to METAPROC_AUTH_POOL above.
    if config.auth_account == "claude-code-cli" and pool_user and config.auth_include_labels:
        primary_label = config.auth_include_labels[0]
        oauth_secret_ref = (
            f"projects/{config.gcp.project}/secrets/"
            f"claude-code-auth-{pool_user}-{primary_label}/versions/latest"
        )
        secret_env_vars["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_secret_ref

    # Pi CLI models config.
    pi_models = build_pi_models_json(config.gcp.project)
    if pi_models:
        env_vars["METAPROC_PI_MODELS_JSON"] = pi_models

    env_vars.update(get_bootstrap_env_vars())

    # Diagnostic-only opt-in: METAPROC_SKIP_PREFLIGHT_PROBE=1 lets a cloud
    # debug dispatch skip the auth pool's pre-fan-out probe so process-item
    # actually fires and writes its invocation.json sidecar even when alt1+alt2
    # are cooling. Without this propagation the env var stops at the local
    # CLI; the orchestrator container would still run the probe, fail, and
    # never give us the worker-side claude argv we are trying to capture.
    # See plan-2026-05-01 § Phase 3.5.
    skip_probe = os.environ.get("METAPROC_SKIP_PREFLIGHT_PROBE", "")
    if skip_probe:
        env_vars["METAPROC_SKIP_PREFLIGHT_PROBE"] = skip_probe

    # Build Batch job.
    # Use entrypoint (not commands) to override the Dockerfile's ENTRYPOINT
    # which defaults to worker_entrypoint.
    env_kwargs: dict[str, object] = {"variables": env_vars}
    if secret_env_vars:
        env_kwargs["secret_variables"] = secret_env_vars

    runnables = build_batch_runnables(
        config.gcp,
        container_kwargs={
            "image_uri": config.gcp.container_image,
            "entrypoint": "python",
            "commands": ["-m", "metaproc.cloud.gcp.orchestrator_entrypoint"],
        },
        env_kwargs=env_kwargs,
    )

    task_spec = TaskSpec(
        runnables=runnables,
        max_run_duration=f"{config.max_duration_s}s",
        compute_resource=build_compute_resource(config.orchestrator_machine_type),
    )

    task_group = TaskGroup(
        task_spec=task_spec,
        task_count=1,
        parallelism=1,
    )

    # Orchestrator uses standard VMs (not spot) to avoid preemption killing the DAG.
    instances = AllocationPolicy.InstancePolicyOrTemplate(
        policy=AllocationPolicy.InstancePolicy(
            machine_type=config.orchestrator_machine_type,
            provisioning_model=AllocationPolicy.ProvisioningModel.STANDARD,
            boot_disk=AllocationPolicy.Disk(size_gb=config.gcp.boot_disk_gb),
        ),
    )

    network_interfaces: list[AllocationPolicy.NetworkInterface] = []
    if config.gcp.network:
        ni = AllocationPolicy.NetworkInterface(network=config.gcp.network)
        if config.gcp.subnetwork:
            ni.subnetwork = config.gcp.subnetwork
        network_interfaces.append(ni)

    network_policy = None
    if network_interfaces:
        network_policy = AllocationPolicy.NetworkPolicy(
            network_interfaces=network_interfaces,
        )

    allocation_policy = AllocationPolicy(
        instances=[instances],
        network=network_policy,
    )

    if config.gcp.service_account_email:
        from google.cloud.batch_v1.types.job import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
            ServiceAccount,
        )

        allocation_policy.service_account = ServiceAccount(
            email=config.gcp.service_account_email,
        )

    nonce = _secrets.token_hex(3)
    ts = str(int(time.time()))
    job_id = build_job_id(
        prefix="mp-orch",
        run_id=run_id,
        suffix=f"{ts}-{nonce}",
    )

    # Labels go on both Job.labels and allocationPolicy.labels so they
    # propagate to VM/disk resources for billing and monitoring.
    job_labels = {
        "metaproc-role": "orchestrator",
        "metaproc-run-id": sanitize_label(run_id),
        "metaproc-variant": sanitize_label(config.variant),
    }
    allocation_policy.labels = job_labels
    job = Job(
        task_groups=[task_group],
        allocation_policy=allocation_policy,
        logs_policy=LogsPolicy(destination=LogsPolicy.Destination.CLOUD_LOGGING),
        labels=job_labels,
    )

    parent = f"projects/{config.gcp.project}/locations/{config.gcp.region}"
    request = batch_v1.CreateJobRequest(
        parent=parent,
        job_id=job_id,
        job=job,
    )

    log.info("Submitting orchestrator Batch job: %s", job_id)
    created_job = await asyncio.to_thread(client.create_job, request)
    job_name = created_job.name
    log.info("Orchestrator job submitted: %s", job_name)

    # Poll until terminal state.
    terminal_states = {
        JobStatus.State.SUCCEEDED,
        JobStatus.State.FAILED,
        JobStatus.State.CANCELLED,
    }

    while True:
        await asyncio.sleep(config.poll_interval)

        try:
            job_status = await get_job_with_retry(client, batch_v1, job_name)
        except Exception:
            log.exception("Failed to poll orchestrator job %s after retries", job_name)
            continue

        state = job_status.status.state
        state_name = JobStatus.State(state).name
        log.info("Orchestrator job %s: %s", job_id, state_name)

        if state in terminal_states:
            exit_code = 0 if state == JobStatus.State.SUCCEEDED else 1
            return OrchestratorResult(
                job_name=job_name,
                job_id=job_id,
                state=state_name,
                exit_code=exit_code,
            )
