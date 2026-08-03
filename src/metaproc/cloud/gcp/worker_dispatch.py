"""Worker VM dispatch — submit N dense worker VMs for fan-out execution.

Used by ``run-process --backend gcp-worker``. Partitions items across N worker
VMs (round-robin), submits GCP Batch jobs each running
``metaproc run-parallel --backend local``, and polls until all complete.
Results land on Filestore NFS.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import secrets as _secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from strif import atomic_output_file

from metaproc import paths as paths_mod
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
    run_identity_labels,
    sanitize_label,
)
from metaproc.config.env_vars import MetaprocEnv
from metaproc.dispatch.auth_pool_flags import AuthPoolFlags
from metaproc.dispatch.repo_sync_payload import RepoSyncPayload
from metaproc.errors import CLIError
from metaproc.io.claimed_items import clear_claimed_items, collect_claimed_items, read_claimed_items
from metaproc.io.dispatch_manifest import (
    append_dispatch_manifest,
    read_dispatch_manifest,
    write_dispatch_manifest,
)
from metaproc.paths import POOL_STATUS_FILE, SCALE_STATE_FILE, step_state_dir
from metaproc.plugins.discovery import get_bootstrap_env_vars
from metaproc.runpool.status import read_scale_state, read_status

log = logging.getLogger(__name__)

# Default worker VM sizing.
DEFAULT_MACHINE_TYPE = "n2-highmem-8"
# 50 per worker × 2 workers = 100 total concurrent items.
# At 50/worker, observed ~1.0x runs/record (no retry amplification).
# At higher concurrency, DSQ 429s cause retry cascades (7x+ runs/record).
# Keep this aligned with the worker image's mounted workspace.
DEFAULT_MAX_CONCURRENCY_PER_WORKER = 50
DEFAULT_NUM_WORKERS = 2
MAX_INLINE_ITEM_CONTEXTS_BYTES = 32_000


@dataclass(frozen=True)
class WorkerDispatchConfig:
    """Parameters for a worker dispatch operation.

    ``auth_flags`` is the resolved auth-pool dispatch config the
    orchestrator (run-process) wants every worker to use. Built once at
    the top of run-process so the worker leg's ``--auth-backend`` is the
    spec-correct default (``gcp-secret-manager`` for cloud workers) even
    when the operator omitted ``--auth-backend`` on the outer CLI. An
    empty :class:`AuthPoolFlags` means "no pool dispatch configured" —
    workers fall back to the legacy single-credential bootstrap.
    """

    gcp: GCPBatchConfig
    num_workers: int = DEFAULT_NUM_WORKERS
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY_PER_WORKER
    initial_concurrency: int | None = None
    max_retries: int | None = None
    poll_interval: int = 60
    spot: bool = True
    variant: str = ""
    adapter_config_json: str = ""
    auth_flags: AuthPoolFlags = field(default_factory=AuthPoolFlags)


@dataclass
class WorkerResult:
    """Result from a single worker VM."""

    worker_id: int
    job_name: str
    job_id: str
    items_count: int
    state: str
    exit_code: int
    error_detail: str = ""


class WorkerJobManifest(TypedDict):
    """Worker job metadata persisted for resume adoption."""

    worker_id: str
    job_name: str
    job_id: str
    items_count: str
    items: NotRequired[list[str]]


def build_gcp_config_from_env(
    *,
    machine_type: str = "",
    spot: bool = True,
) -> GCPBatchConfig:
    """Build a GCPBatchConfig from environment variables.

    Raises ValueError if required env vars are missing.
    """
    project = MetaprocEnv.METAPROC_GCP_PROJECT.read_str()
    container_image = MetaprocEnv.METAPROC_GCP_CONTAINER_IMAGE.read_str()
    filestore_server = MetaprocEnv.METAPROC_GCP_FILESTORE_SERVER.read_str()

    effective_machine_type = machine_type or MetaprocEnv.METAPROC_GCP_MACHINE_TYPE.read_str(
        default=DEFAULT_MACHINE_TYPE
    )

    return GCPBatchConfig(
        project=project,
        region=MetaprocEnv.METAPROC_GCP_REGION.read_str(default="us-central1"),
        container_image=container_image,
        machine_type=effective_machine_type,
        spot=spot,
        boot_disk_gb=MetaprocEnv.METAPROC_GCP_BOOT_DISK_GB.read_int(default=50),
        max_run_duration_s=MetaprocEnv.METAPROC_GCP_MAX_RUN_DURATION_S.read_int(default=28800),
        service_account_email=MetaprocEnv.METAPROC_GCP_SERVICE_ACCOUNT.read_str(default=""),
        network=MetaprocEnv.METAPROC_GCP_NETWORK.read_str(default=""),
        subnetwork=MetaprocEnv.METAPROC_GCP_SUBNETWORK.read_str(default=""),
        runs_dir=MetaprocEnv.RUNS_DIR.read_str(default=""),
        filestore_server=filestore_server,
        filestore_share=MetaprocEnv.METAPROC_GCP_FILESTORE_SHARE.read_str(default="/metaproc_runs"),
        filestore_mount_path=MetaprocEnv.METAPROC_GCP_FILESTORE_MOUNT_PATH.read_str(
            default="/mnt/filestore"
        ),
    )


def partition_items(
    item_contexts: list[dict[str, str]],
    each: str,
    num_workers: int,
) -> tuple[list[list[str]], list[list[dict[str, str]]]]:
    """Partition items across workers using round-robin distribution.

    Returns (item_partitions, context_partitions).
    """
    total = len(item_contexts)
    effective_workers = min(num_workers, total)
    partitions: list[list[str]] = [[] for _ in range(effective_workers)]
    context_partitions: list[list[dict[str, str]]] = [[] for _ in range(effective_workers)]

    for i, ctx in enumerate(item_contexts):
        partitions[i % effective_workers].append(ctx[each])
        context_partitions[i % effective_workers].append(ctx)

    return partitions, context_partitions


async def dispatch_to_workers(
    *,
    item_contexts: list[dict[str, str]],
    each: str,
    config: WorkerDispatchConfig,
    step: str,
    process_spec_rel: str,
    variables: dict[str, str],
    out: Any,
    nfs_run_dir: str = "",
    run_dir: Path | None = None,
) -> list[WorkerResult]:
    """Partition items across N worker VMs and run to completion.

    This is the main entry point for gcp-worker dispatch from run-process.
    Submits GCP Batch jobs, polls until all complete, returns results.

    If *nfs_run_dir* is set (path to the run directory on the shared NFS
    mount), the poll loop reads ``runpool-status.yaml`` from workers'
    state dirs to report live progress and extract error details on failure.
    """
    partitions, context_partitions = partition_items(item_contexts, each, config.num_workers)
    effective_workers = len(partitions)
    total = len(item_contexts)

    out.progress(f"\n  gcp-worker dispatch: {total} items -> {effective_workers} worker(s)")
    out.progress(f"    Machine type: {config.gcp.machine_type}")
    out.progress(f"    Max concurrency per worker: {config.max_concurrency}")
    out.progress(f"    Variant: {config.variant}")
    out.progress(f"    Spot: {config.spot}")
    for wi, partition in enumerate(partitions):
        out.progress(f"    Worker {wi}: {len(partition)} items")

    vars_json = json.dumps(variables)

    worker_jobs = await _submit_workers(
        partitions=partitions,
        context_partitions=context_partitions,
        config=config,
        step=step,
        process_spec_rel=process_spec_rel,
        vars_json=vars_json,
        run_dir=run_dir,
        out=out,
        starting_worker_id=0,
    )

    out.progress(f"\n  Submitted {len(worker_jobs)} worker job(s). Polling for completion...")

    # Persist dispatch manifest so a resumed orchestrator can adopt these workers.
    if run_dir is not None:
        write_dispatch_manifest(
            run_dir,
            step,
            worker_jobs=worker_jobs,
            num_items=total,
            variant=config.variant,
        )

    return await _poll_workers(
        worker_jobs,
        config.gcp,
        config.poll_interval,
        out,
        nfs_run_dir=nfs_run_dir,
    )


def _step_scale_state_path(run_dir: Path, step: str) -> Path:
    """Return ``<run>/.state/steps/<step>/scale-state.yaml``."""
    return step_state_dir(run_dir, step) / SCALE_STATE_FILE


def _resolve_desired_topology(
    *,
    run_dir: Path,
    step: str,
    config: WorkerDispatchConfig,
) -> tuple[int, int]:
    """Read desired worker topology from step scale-state.yaml if present."""
    path = _step_scale_state_path(run_dir, step)
    if not path.exists():
        return config.num_workers, config.max_concurrency

    try:
        state = read_scale_state(path)
    except Exception:
        log.warning("Could not read step scale-state for %s", step, exc_info=True)
        return config.num_workers, config.max_concurrency

    desired_workers = state.desired_workers or config.num_workers
    desired_cap = state.desired_max_concurrency or config.max_concurrency
    return desired_workers, desired_cap


def _claimed_items_for_worker(worker: WorkerJobManifest, *, run_dir: Path, step: str) -> set[str]:
    """Return active claims for one worker, falling back to manifest items."""
    worker_id = worker["worker_id"]
    record = read_claimed_items(run_dir, step, worker_id)
    if record is not None and record.items:
        return set(record.items)

    items = worker.get("items")
    if isinstance(items, list):
        return {str(item) for item in items}
    return set()


async def _inspect_worker_jobs(
    worker_jobs: list[WorkerJobManifest],
) -> tuple[list[WorkerJobManifest], list[WorkerJobManifest]]:
    """Split manifest workers into active vs terminal jobs."""
    from google.api_core import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
        exceptions as google_exceptions,
    )
    from google.cloud import batch_v1  # noqa: PLC0415 -- optional [gcp-batch] dependency
    from google.cloud.batch_v1.types import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
        JobStatus,
    )

    client = batch_v1.BatchServiceClient()
    terminal_states = {
        JobStatus.State.SUCCEEDED,
        JobStatus.State.FAILED,
        JobStatus.State.CANCELLED,
    }
    active_workers: list[WorkerJobManifest] = []
    terminal_workers: list[WorkerJobManifest] = []

    for worker in worker_jobs:
        job_name = worker.get("job_name", "")
        if not job_name:
            terminal_workers.append(worker)
            continue
        try:
            job = await asyncio.to_thread(
                client.get_job,
                batch_v1.GetJobRequest(name=job_name),
            )
        except google_exceptions.NotFound:
            terminal_workers.append(worker)
            continue
        except Exception as exc:
            log.warning("Could not inspect worker job %s", job_name, exc_info=True)
            raise CLIError(
                "Could not inspect an existing gcp-worker job while resuming. "
                f"Refusing to redispatch because state is unknown: {job_name}. "
                "Retry when GCP Batch is reachable."
            ) from exc

        if job.status.state in terminal_states:
            terminal_workers.append(worker)
        else:
            active_workers.append(worker)

    return active_workers, terminal_workers


def _parse_worker_manifest(raw_workers: list[object]) -> list[WorkerJobManifest]:
    """Parse raw manifest dicts into typed WorkerJobManifest entries."""
    worker_jobs: list[WorkerJobManifest] = []
    for raw_worker in raw_workers:
        if not isinstance(raw_worker, dict):
            continue
        worker_id = raw_worker.get("worker_id")
        job_name = raw_worker.get("job_name")
        job_id = raw_worker.get("job_id")
        items_count = raw_worker.get("items_count")
        if not isinstance(worker_id, str):
            continue
        if not isinstance(job_name, str):
            continue
        if not isinstance(job_id, str):
            continue
        if not isinstance(items_count, str):
            continue
        parsed_worker: WorkerJobManifest = {
            "worker_id": worker_id,
            "job_name": job_name,
            "job_id": job_id,
            "items_count": items_count,
        }
        items = raw_worker.get("items")
        if isinstance(items, list):
            parsed_worker["items"] = [str(item) for item in items]
        worker_jobs.append(parsed_worker)
    return worker_jobs


async def reconcile_dispatched_workers(
    *,
    run_dir: Path,
    step: str,
    item_contexts: list[dict[str, str]],
    each: str,
    config: WorkerDispatchConfig,
    process_spec_rel: str,
    variables: dict[str, str],
    out: Any,
    nfs_run_dir: str = "",
    wait_for_completion: bool = True,
) -> list[WorkerResult] | None:
    """Adopt active workers and optionally submit additional workers for unclaimed items."""

    manifest = read_dispatch_manifest(run_dir, step)
    if manifest is None:
        return None

    raw_workers = manifest.get("workers")
    if not isinstance(raw_workers, list):
        raise CLIError(
            f"Dispatch manifest for step '{step}' is corrupt: 'workers' field "
            f"is not a list. Refusing to redispatch — manual inspection required."
        )

    worker_jobs = _parse_worker_manifest(raw_workers)
    if not worker_jobs:
        raise CLIError(
            f"Dispatch manifest for step '{step}' exists but has no parseable "
            f"worker entries ({len(raw_workers)} raw entries). Refusing to "
            f"redispatch — items may already be in flight; manual inspection required."
        )

    active_workers, terminal_workers = await _inspect_worker_jobs(worker_jobs)
    for worker in terminal_workers:
        clear_claimed_items(run_dir, step, worker_id=worker["worker_id"])
    actionable_items = {
        str(item_context[each])
        for item_context in item_contexts
        if each in item_context and item_context[each]
    }
    desired_workers, desired_cap = _resolve_desired_topology(
        run_dir=run_dir,
        step=step,
        config=config,
    )

    active_worker_ids = [worker["worker_id"] for worker in active_workers]
    adopted_items = collect_claimed_items(
        run_dir,
        step,
        worker_ids=set(active_worker_ids),
    )
    if not adopted_items:
        for worker in active_workers:
            adopted_items.update(_claimed_items_for_worker(worker, run_dir=run_dir, step=step))
    adopted_items = {item for item in adopted_items if item in actionable_items}

    active_count = len(active_workers)
    desired_count = max(active_count, min(desired_workers, len(actionable_items)))
    additional_workers = max(0, desired_count - active_count)
    additional_contexts = [
        item_context
        for item_context in item_contexts
        if str(item_context.get(each, "")) not in adopted_items
    ]

    combined_workers = list(active_workers)
    if additional_workers > 0 and additional_contexts:
        numeric_active_ids = [
            int(worker_id) for worker_id in active_worker_ids if worker_id.isdigit()
        ]
        next_worker_id = (max(numeric_active_ids) + 1) if numeric_active_ids else 0
        vars_json = json.dumps(variables)
        submit_config = dataclasses.replace(
            config,
            num_workers=additional_workers,
            max_concurrency=desired_cap,
        )
        partitions, context_partitions = partition_items(
            additional_contexts, each, additional_workers
        )
        new_workers = await _submit_workers(
            partitions=partitions,
            context_partitions=context_partitions,
            config=submit_config,
            step=step,
            process_spec_rel=process_spec_rel,
            vars_json=vars_json,
            run_dir=run_dir,
            out=out,
            starting_worker_id=next_worker_id,
        )
        append_dispatch_manifest(run_dir, step, worker_jobs=new_workers)
        combined_workers.extend(new_workers)
        out.progress(
            f"  Step '{step}': scaled from {active_count} to {len(combined_workers)} worker(s) "
            f"for {len(additional_contexts)} unclaimed item(s)"
        )
    elif active_workers:
        out.progress(
            f"  Step '{step}': adopting {len(active_workers)} active worker(s) "
            f"covering {len(adopted_items)} claimed item(s)"
        )

    if not wait_for_completion:
        return []

    if not combined_workers:
        return []
    return await _poll_workers(
        combined_workers,
        config.gcp,
        config.poll_interval,
        out,
        nfs_run_dir=nfs_run_dir,
    )


async def _submit_workers(
    *,
    partitions: list[list[str]],
    context_partitions: list[list[dict[str, str]]],
    config: WorkerDispatchConfig,
    step: str,
    process_spec_rel: str,
    vars_json: str,
    run_dir: Path | None,
    out: Any,
    starting_worker_id: int = 0,
) -> list[WorkerJobManifest]:
    """Submit GCP Batch jobs for each worker partition."""
    from google.cloud import batch_v1  # noqa: PLC0415 -- optional [gcp-batch] dependency
    from google.cloud.batch_v1.types import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
        AllocationPolicy,
        Job,
        LogsPolicy,
        TaskGroup,
        TaskSpec,
    )

    client = batch_v1.BatchServiceClient()
    worker_jobs: list[WorkerJobManifest] = []

    runtime_vars = apply_container_runs_dir(config.gcp, json.loads(vars_json))
    run_id = runtime_vars.get("RUN_ID", "unknown")
    vars_json = json.dumps(runtime_vars)

    for partition_index, partition in enumerate(partitions):
        wi = starting_worker_id + partition_index
        items_csv = ",".join(partition)
        contexts = context_partitions[partition_index]
        contexts_json = json.dumps(contexts)

        env_vars: dict[str, str] = {
            "METAPROC_WORKER_ITEMS": items_csv,
            "METAPROC_PROCESS_SPEC": process_spec_rel,
            "METAPROC_STEP": step,
            "METAPROC_VARS": vars_json,
            "METAPROC_MAX_CONCURRENCY": str(config.max_concurrency),
            "METAPROC_VARIANT": config.variant,
            "GOOGLE_CLOUD_PROJECT": config.gcp.project,
            "GCLOUD_PROJECT": config.gcp.project,
            "CLOUDSDK_CORE_PROJECT": config.gcp.project,
            # Consumed by pi-cli's `google-vertex` API provider (@google/genai SDK).
            # "global" resolves to https://aiplatform.googleapis.com/ under the SDK,
            # which is the only hostname serving Gemini 3.x preview models. See
            # metaproc/docs/runbooks/adapter-compatibility.runbook.md.
            "GOOGLE_CLOUD_LOCATION": "global",
        }

        if config.max_retries is not None:
            env_vars["METAPROC_MAX_RETRIES"] = str(config.max_retries)
        if config.initial_concurrency is not None:
            env_vars["METAPROC_INITIAL_CONCURRENCY"] = str(config.initial_concurrency)
        if config.adapter_config_json:
            env_vars["METAPROC_ADAPTER_CONFIG"] = config.adapter_config_json

        # Worker ID — used by run-parallel to scope pool state per worker,
        # preventing multiple workers from clobbering shared state on NFS.
        env_vars["METAPROC_WORKER_ID"] = str(wi)

        if len(contexts_json.encode("utf-8")) > MAX_INLINE_ITEM_CONTEXTS_BYTES:
            if run_dir is None:
                raise CLIError(
                    "worker item contexts payload exceeds inline env limit but run_dir is unavailable"
                )
            # Spill files require a shared filesystem that both the orchestrator
            # (where we write) and the worker container (where we read) can see.
            # Without Filestore, the orchestrator's local run_dir is invisible
            # to the worker VM and the worker would fail to load the payload.
            if not config.gcp.filestore_server:
                raise CLIError(
                    f"worker {wi} item contexts payload "
                    f"({len(contexts_json.encode('utf-8'))} bytes) exceeds inline "
                    f"env limit but Filestore is not configured; spill files "
                    f"require a shared filesystem between orchestrator and workers"
                )
            payload_dir = step_state_dir(run_dir, step) / "worker_payloads"
            payload_dir.mkdir(parents=True, exist_ok=True)
            payload_path = payload_dir / f"worker-{wi}-item-contexts.json"
            with atomic_output_file(payload_path) as tmp:
                Path(tmp).write_text(contexts_json)
            env_vars["METAPROC_ITEM_CONTEXTS_FILE"] = str(payload_path)
        else:
            env_vars["METAPROC_ITEM_CONTEXTS"] = contexts_json

        # Auth-pool flags: forward auth-pool config from the orchestrator
        # process into each fan-out worker Batch job. Without this step,
        # the inner run-process knows the flags but each spawned worker
        # runs `run-parallel` blind and falls back to the legacy
        # single-credential bootstrap. AuthPoolFlags centralizes the
        # env-var names and CSV encoding so this site does not hardcode
        # any "METAPROC_AUTH_*" strings.
        #
        # Prefer the explicitly-resolved flags from
        # :class:`WorkerDispatchConfig`. Hybrid (`run-process --backend
        # gcp-worker`) only has these as CLI flags, never as env vars,
        # so a ``from_env()`` fallback at this site silently dropped the
        # entire auth chain. The ``from_env()`` fallback is preserved
        # for the full-cloud orchestrator container path, which
        # genuinely sources METAPROC_AUTH_* from the Batch job env.
        auth_flags = (
            config.auth_flags
            if config.auth_flags.is_pool_dispatch_enabled()
            else AuthPoolFlags.from_env()
        )
        env_vars.update(auth_flags.to_env_vars())

        # Pool owner: same propagation as orchestrator_dispatch. Worker
        # `run-parallel` resolves pool_user via METAPROC_AUTH_POOL → USER →
        # "unknown" inside ``_build_pool_dispatch_template``. USER is empty
        # in the worker container, so without explicit propagation the
        # GcpSecretManagerBackend looks for ``claude-code-auth-unknown-<label>``
        # and 404s. Propagate the locally-resolved pool_user.
        pool_user = MetaprocEnv.METAPROC_AUTH_POOL.read_str(
            default=""
        ) or MetaprocEnv.USER.read_str(default="")
        if pool_user:
            env_vars["METAPROC_AUTH_POOL"] = pool_user

        # GCP project: worker `run-parallel` calls
        # ``_build_pool_dispatch_template`` which constructs
        # ``gcp_backend(project, ...)``. ``project`` resolves from
        # ``METAPROC_GCP_PROJECT``; if empty, the function raises CLIError.
        # We already pass ``GOOGLE_CLOUD_PROJECT=<project>`` for the
        # google-genai SDK, but metaproc reads METAPROC_GCP_PROJECT
        # specifically — propagate it explicitly so the worker can build
        # the SM-backed pool.
        env_vars["METAPROC_GCP_PROJECT"] = config.gcp.project

        # Filestore NFS — RUNS_DIR points to <mount_path>/runs so run-id
        # directories live in a runs/ subdirectory, not at the share root.
        # Set unconditionally when filestore is configured (RF-5): the
        # orchestrator may not have runs_dir set locally but workers still
        # need RUNS_DIR to resolve run directories on the NFS mount.
        runs_dir = get_container_runs_dir(config.gcp)
        if config.gcp.filestore_server:
            env_vars["RUNS_DIR"] = runs_dir
            env_vars["METAPROC_GCP_FILESTORE_SERVER"] = config.gcp.filestore_server
        elif runs_dir:
            env_vars["RUNS_DIR"] = runs_dir

        env_vars.update(RepoSyncPayload.from_env().to_env_vars())

        # Credentials injected via Secret Manager only — plaintext values would
        # persist in the Batch job spec (visible via `gcloud batch jobs describe`
        # and the API). See `GCP_SECRET_REFS` in batch_backend.py for the table.
        secret_env_vars: dict[str, str] = {}
        for plaintext_env, secret_env, description in GCP_SECRET_REFS:
            ref = resolve_gcp_secret_ref(plaintext_env, secret_env, description)
            if ref:
                secret_env_vars[plaintext_env] = ref

        # Pi CLI models config.
        pi_models = build_pi_models_json(config.gcp.project)
        if pi_models:
            env_vars["METAPROC_PI_MODELS_JSON"] = pi_models

        env_vars.update(get_bootstrap_env_vars())

        # Build Batch job.
        env_kwargs: dict[str, object] = {"variables": env_vars}
        if secret_env_vars:
            env_kwargs["secret_variables"] = secret_env_vars

        runnables = build_batch_runnables(
            config.gcp,
            container_kwargs={"image_uri": config.gcp.container_image},
            env_kwargs=env_kwargs,
        )

        task_spec = TaskSpec(
            runnables=runnables,
            max_run_duration=f"{config.gcp.max_run_duration_s}s",
            compute_resource=build_compute_resource(config.gcp.machine_type),
        )

        task_group = TaskGroup(
            task_spec=task_spec,
            task_count=1,
            parallelism=1,
        )

        instances = AllocationPolicy.InstancePolicyOrTemplate(
            policy=AllocationPolicy.InstancePolicy(
                machine_type=config.gcp.machine_type,
                provisioning_model=(
                    AllocationPolicy.ProvisioningModel.SPOT
                    if config.spot
                    else AllocationPolicy.ProvisioningModel.STANDARD
                ),
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
            prefix="mp-worker",
            run_id=run_id,
            suffix=f"w{wi}-{ts}-{nonce}",
        )

        # Labels go on both Job.labels and allocationPolicy.labels so they
        # propagate to VM/disk resources for billing and monitoring.
        job_labels = {
            "metaproc-role": "worker",
            "metaproc-dispatch": "worker",
            **run_identity_labels(run_id),
            "metaproc-step": sanitize_label(step),
            "metaproc-variant": sanitize_label(config.variant),
            "metaproc-worker-id": str(wi),
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

        log.info("Submitting worker %d with %d items: %s", wi, len(partition), job_id)
        created_job = await asyncio.to_thread(client.create_job, request)
        job_name = created_job.name

        worker_jobs.append(
            {
                "worker_id": str(wi),
                "job_name": job_name,
                "job_id": job_id,
                "items_count": str(len(partition)),
                "items": partition,
            }
        )
        out.progress(f"    Submitted worker {wi}: {job_id} ({len(partition)} items)")

    return worker_jobs


def _find_worker_status_files(nfs_run_dir: str) -> list[Path]:
    """Find all pool status files, including worker-scoped V2 and legacy ones."""

    state_dir = paths_mod.run_state_dir(Path(nfs_run_dir))
    candidates: list[Path] = []

    # Unscoped pool status used by direct run-parallel invocations.
    shared = state_dir / POOL_STATUS_FILE
    if shared.exists():
        candidates.append(shared)

    # V2 worker-scoped: .state/workers/worker-*/runpool-status.yaml
    workers_dir = state_dir / paths_mod.WORKERS_SUBDIR
    if workers_dir.exists():
        for worker_dir in sorted(workers_dir.glob("worker-*")):
            worker_status = worker_dir / POOL_STATUS_FILE
            if worker_status.exists():
                candidates.append(worker_status)

    # Legacy worker-scoped: .state/worker-*/runpool-status.yaml
    if state_dir.exists():
        for worker_dir in sorted(state_dir.glob("worker-*")):
            worker_status = worker_dir / POOL_STATUS_FILE
            if worker_status.exists() and worker_status not in candidates:
                candidates.append(worker_status)

    return candidates


def _read_nfs_pool_status(nfs_run_dir: str) -> tuple[int, int, int]:
    """Read runpool-status.yaml from NFS to get live worker progress.

    Aggregates across all worker-scoped state dirs (RF-3).
    Returns (completed_count, failed_count, active_count).
    Falls back to (0, 0, 0) if no files exist or can't be parsed.
    """

    total_completed, total_failed, total_active = 0, 0, 0
    for status_path in _find_worker_status_files(nfs_run_dir):
        try:
            status = read_status(status_path)
            total_completed += status.completed_count
            total_failed += status.failed_count
            total_active += status.active_count
        except Exception:
            log.debug("Could not read NFS pool status at %s", status_path, exc_info=True)
    return total_completed, total_failed, total_active


def _read_nfs_error_detail(nfs_run_dir: str) -> str:
    """Extract error summary from worker pool status on NFS.

    Aggregates across all worker-scoped state dirs (RF-3).
    """

    errors: list[str] = []
    for status_path in _find_worker_status_files(nfs_run_dir):
        try:
            status = read_status(status_path)
            for proc in status.recent_completions:
                if proc.exit_code and proc.exit_code != 0:
                    detail = proc.kill_reason or f"exit_code={proc.exit_code}"
                    errors.append(f"{proc.label}: {detail}")
            if status.failure_counts.rate_limited:
                errors.append(f"rate_limited={status.failure_counts.rate_limited}")
            if status.failure_counts.server_error:
                errors.append(f"server_error={status.failure_counts.server_error}")
        except Exception:
            log.debug("Could not read NFS error detail at %s", status_path, exc_info=True)
    return "; ".join(errors[:5])


async def _poll_workers(
    worker_jobs: list[WorkerJobManifest],
    gcp_config: GCPBatchConfig,
    poll_interval: int,
    out: Any,
    nfs_run_dir: str = "",
) -> list[WorkerResult]:
    """Poll worker Batch jobs until all complete.

    If *nfs_run_dir* points to the shared NFS run directory, also reads
    ``runpool-status.yaml`` to report live progress counts and extract
    error details for failed workers.
    """
    from google.cloud import batch_v1  # noqa: PLC0415 -- optional [gcp-batch] dependency
    from google.cloud.batch_v1.types import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
        JobStatus,
    )

    client = batch_v1.BatchServiceClient()

    terminal_states = {
        JobStatus.State.SUCCEEDED,
        JobStatus.State.FAILED,
        JobStatus.State.CANCELLED,
    }

    pending = {w["job_name"]: w for w in worker_jobs}
    results: list[WorkerResult] = []

    while pending:
        await asyncio.sleep(poll_interval)

        completed_this_round: list[str] = []
        for job_name, w in pending.items():
            try:
                job = await get_job_with_retry(client, batch_v1, job_name)
            except Exception:
                log.exception("Failed to poll worker %s after retries", job_name)
                continue

            state = job.status.state
            if state in terminal_states:
                exit_code = 0 if state == JobStatus.State.SUCCEEDED else 1
                error_detail = ""
                if exit_code != 0 and nfs_run_dir:
                    error_detail = _read_nfs_error_detail(nfs_run_dir)
                result = WorkerResult(
                    worker_id=int(w["worker_id"]),
                    job_name=job_name,
                    job_id=w["job_id"],
                    items_count=int(w["items_count"]),
                    state=JobStatus.State(state).name,
                    exit_code=exit_code,
                    error_detail=error_detail,
                )
                results.append(result)
                completed_this_round.append(job_name)
                msg = f"    Worker {w['worker_id']} {result.state} ({w['items_count']} items)"
                if error_detail:
                    msg += f" — {error_detail}"
                out.progress(msg)

        for jn in completed_this_round:
            del pending[jn]

        if pending:
            running = len(pending)
            done = len(results)
            progress_msg = f"    ... {running} worker(s) still running, {done} complete"
            if nfs_run_dir:
                completed, failed, active = _read_nfs_pool_status(nfs_run_dir)
                if completed or failed or active:
                    progress_msg += f" (NFS: {completed} done, {failed} failed, {active} active)"
            out.progress(progress_msg)

    return results
