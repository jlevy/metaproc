"""GCP Batch shared utilities — config, job helpers, and label sanitization.

Used by ``worker_dispatch.py`` and ``orchestrator_dispatch.py`` for
submitting multi-VM worker and orchestrator Batch jobs.

Requires the ``[gcp-batch]`` optional extra (which installs the GCP client
libraries): ``uv sync --extra gcp-batch``

Configuration is passed via ``GCPBatchConfig``.  Credentials are resolved
via the existing ``gcp_credentials`` module (``google.auth.default()`` chain).
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import os as _os
import re
import re as _re
import shlex
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, cast

from metaproc.config.env_vars import MetaprocEnv

log = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────


@dataclass(frozen=True)
class GCPBatchConfig:
    """Configuration for GCP Batch job creation."""

    project: str
    region: str = "us-central1"
    container_image: str = ""
    machine_type: str = "e2-standard-4"
    spot: bool = True
    boot_disk_gb: int = 50
    max_run_duration_s: int = 3600
    service_account_email: str = ""
    network: str = ""
    subnetwork: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    # Secret Manager secrets to mount as env vars.
    # Format: {"ENV_VAR_NAME": "projects/P/secrets/S/versions/V"}
    secret_env_vars: dict[str, str] = field(default_factory=dict)
    # RUNS_DIR: base path for run artifacts inside the container.
    # When set, passed as RUNS_DIR env var so {{RUNS_DIR}} resolves in process specs.
    # When filestore_server is also set, Batch dispatch mounts Filestore on the
    # host and bind-mounts it into the container at this path.
    runs_dir: str = ""
    # Filestore NFS configuration (optional — enables NFS volume mount).
    filestore_server: str = ""  # Filestore IP address
    filestore_share: str = "/metaproc_runs"  # Filestore share name
    # NFS mount path inside the container.  This is a container-level mount
    # point set via the Batch API Volume spec — not subject to COS host-level
    # path restrictions.  Standardized to /mnt/filestore across all VM types
    # (workers, orchestrators, browser host) for consistent path resolution.
    # RUNS_DIR is set to <mount_path>/runs so run-id directories live in a
    # runs/ subdirectory on the share, not at the share root.
    filestore_mount_path: str = "/mnt/filestore"
    # Maximum time (seconds) a job may stay in QUEUED/SCHEDULED state before
    # being cancelled.  Prevents Spot VM queue starvation from blocking the pool.
    queue_timeout_s: int = 1800


# Plaintext env var → Secret Manager ref env var → human description.
# Any credential injected into a Batch job must go through Secret Manager;
# plaintext values would persist in the job spec (`gcloud batch jobs describe`).
#
# Phase 10 follow-up: the cohort lives as a typed
# :class:`SecretRefSet` in metaproc.dispatch.secret_refs. The legacy
# ``GCP_SECRET_REFS`` tuple shape is preserved here as a back-compat
# alias so existing imports keep working; new call sites should use
# ``SecretRefSet.all_known()`` directly.
from metaproc.dispatch.secret_refs import SecretRef, SecretRefSet  # noqa: E402

GCP_SECRET_REFS: tuple[tuple[str, str, str], ...] = SecretRefSet.all_known().as_tuples()


def resolve_gcp_secret_ref(
    plaintext_env: str,
    secret_env: str,
    description: str,
) -> str:
    """Return the Secret Manager resource name from ``secret_env``.

    Refuses plaintext leakage: if ``plaintext_env`` is set without ``secret_env``,
    raises ``RuntimeError``. Returns ``""`` when neither is set.

    Thin wrapper over :meth:`SecretRef.resolve` so legacy callers that
    pass three positional strings keep working.
    """
    return SecretRef(
        plaintext_env=plaintext_env,
        secret_env=secret_env,
        description=description,
    ).resolve()


def _build_secret_env_vars() -> dict[str, str]:
    """Build secret env var mapping from METAPROC_GCP_SECRET_* env vars."""
    return SecretRefSet.all_known().to_secret_variables()


# ── ComputeResource derivation ────────────────────────────────────
#
# GCP Batch defaults an unset ``TaskSpec.compute_resource`` to 2000 cpu_milli /
# 2000 memory_mib regardless of the VM's real capacity. That means an
# ``n2-highmem-8`` VM (64 GiB host RAM) gives the container a ``2 GiB`` cgroup
# unless we ask for more. This is the real source of the "memory_ceiling=1 →
# SIGKILL" pattern we saw on long-running items, not model buffering.
#
# Derive a right-sized ``ComputeResource`` from the machine type so the
# container sees the VM's actual capacity, minus a small host reserve for the
# Batch agent + Docker daemon + OS. Operators can override via
# ``METAPROC_GCP_TASK_CPU_MILLI`` and ``METAPROC_GCP_TASK_MEMORY_MIB``.

# Machine-class → GB RAM per vCPU (GCE published standard shapes).
_MACHINE_CLASS_RAM_PER_VCPU_GIB: dict[str, float] = {
    "standard": 4.0,
    "highmem": 8.0,
    "highcpu": 1.0,
    "megamem": 13.0,
    "ultramem": 28.0,
}

# Families that follow the standard <family>-<class>-<N> pattern. Adding a new
# family here is a one-line change if its class ratios match the table above.
_SUPPORTED_FAMILIES: frozenset[str] = frozenset(
    {"e2", "n1", "n2", "n2d", "n4", "c3", "c3d", "c4", "c4a", "t2d", "t2a"}
)

# Host reserve — Batch agent (~100 MiB) + Docker daemon + OS + headroom.
# Deliberately conservative so the container's cgroup isn't fighting the host
# for the last MiB; the scheduler cuts concurrency long before this matters.
HOST_CPU_RESERVE_MILLI: int = 500
HOST_MEMORY_RESERVE_MIB: int = 1024


def infer_compute_resource(machine_type: str) -> tuple[int, int]:
    """Return ``(cpu_milli, memory_mib)`` a container should claim on ``machine_type``.

    Parses GCE machine types (``<family>-<class>-<N>`` or ``custom-CPU-MEM``)
    and reserves :data:`HOST_CPU_RESERVE_MILLI` / :data:`HOST_MEMORY_RESERVE_MIB`
    for the Batch agent + host OS.

    Raises ``ValueError`` on unknown families, unknown classes, or unparsable
    strings — the alternative is silently falling back to Batch's 2 GiB default,
    which is the bug we are fixing.
    """

    spec = machine_type.strip().lower()

    custom = re.fullmatch(r"custom-(\d+)-(\d+)", spec)
    if custom:
        vcpus = int(custom.group(1))
        ram_mib = int(custom.group(2))
    else:
        m = re.fullmatch(r"([a-z][a-z0-9]*)-([a-z]+)-(\d+)", spec)
        if not m:
            raise ValueError(
                f"Cannot parse machine type {machine_type!r}; expected "
                "<family>-<class>-<N> or custom-CPU-MEM"
            )
        family, mclass, n_str = m.group(1), m.group(2), m.group(3)
        if family not in _SUPPORTED_FAMILIES:
            raise ValueError(
                f"Unsupported machine family {family!r} (machine type "
                f"{machine_type!r}). Add it to _SUPPORTED_FAMILIES if the "
                "class ratios match."
            )
        if mclass not in _MACHINE_CLASS_RAM_PER_VCPU_GIB:
            raise ValueError(
                f"Unknown machine class {mclass!r} in {machine_type!r}. "
                f"Known: {sorted(_MACHINE_CLASS_RAM_PER_VCPU_GIB)}"
            )
        vcpus = int(n_str)
        ram_mib = int(vcpus * _MACHINE_CLASS_RAM_PER_VCPU_GIB[mclass] * 1024)

    if vcpus < 1:
        raise ValueError(f"Machine type {machine_type!r} has < 1 vCPU")

    total_cpu_milli = vcpus * 1000
    cpu_milli = max(1000, total_cpu_milli - HOST_CPU_RESERVE_MILLI)
    memory_mib = max(1024, ram_mib - HOST_MEMORY_RESERVE_MIB)
    return cpu_milli, memory_mib


def resolve_compute_resource(machine_type: str) -> tuple[int, int]:
    """Like :func:`infer_compute_resource`, but honors operator env overrides.

    - ``METAPROC_GCP_TASK_CPU_MILLI`` / ``METAPROC_GCP_TASK_MEMORY_MIB``
      override either or both axes.
    - If parsing the machine type fails (unknown family, typo, missing
      override), we log a loud warning and fall back to the legacy Batch
      default (2000 / 2000) rather than crashing the dispatch. The warning
      makes the failure visible; silent defaults are what got us here.
    """

    cpu_override = MetaprocEnv.METAPROC_GCP_TASK_CPU_MILLI.read_str(default="").strip()
    mem_override = MetaprocEnv.METAPROC_GCP_TASK_MEMORY_MIB.read_str(default="").strip()

    inferred_cpu: int | None = None
    inferred_mem: int | None = None
    try:
        inferred_cpu, inferred_mem = infer_compute_resource(machine_type)
    except ValueError as e:
        log.warning(
            "Could not infer compute_resource from machine_type %r (%s); "
            "falling back to GCP Batch default 2000m / 2000 MiB. Set "
            "METAPROC_GCP_TASK_CPU_MILLI and METAPROC_GCP_TASK_MEMORY_MIB to "
            "override, or add the machine type to batch_backend._SUPPORTED_FAMILIES.",
            machine_type,
            e,
        )

    cpu_milli = int(cpu_override) if cpu_override else (inferred_cpu or 2000)
    memory_mib = int(mem_override) if mem_override else (inferred_mem or 2000)
    return cpu_milli, memory_mib


def build_compute_resource(machine_type: str) -> Any:
    """Build a ``ComputeResource`` for use on ``TaskSpec.compute_resource``.

    Import kept inside the function so the ``[gcp-batch]`` extra remains
    optional for non-cloud installs.
    """
    from google.cloud.batch_v1 import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
        ComputeResource,
    )

    cpu_milli, memory_mib = resolve_compute_resource(machine_type)
    log.info(
        "Batch compute_resource for machine_type=%s: cpu_milli=%d memory_mib=%d",
        machine_type,
        cpu_milli,
        memory_mib,
    )
    return ComputeResource(cpu_milli=cpu_milli, memory_mib=memory_mib)


def _rewrite_pi_models_json(raw: str, project: str) -> str:
    """Apply the cloud-dispatch rewrites to a pi-cli models.json string.

    - ``!gcloud auth print-access-token`` apiKey commands become a command that reads
      ``METAPROC_PI_API_KEY`` when supplied and otherwise invokes
      ``gcp-access-token.sh``, so neither path needs gcloud or exposes a token in argv.
    - Any Vertex ``baseUrl`` has its project path normalized to ``project``,
      so a packaged template with a placeholder project stays correct and
      a workstation-authored file still dispatches to the intended project.
    """

    data = _json.loads(raw)
    providers = data.get("providers", {})
    for provider in providers.values():
        api_key = provider.get("apiKey", "")
        if isinstance(api_key, str) and api_key.startswith("!gcloud"):
            provider["apiKey"] = (
                '!sh -c \'if [ -n "$METAPROC_PI_API_KEY" ]; then '
                'printf "%s" "$METAPROC_PI_API_KEY"; else exec gcp-access-token.sh; fi\''
            )
        base_url = provider.get("baseUrl", "")
        if "aiplatform.googleapis.com" in base_url and project:
            provider["baseUrl"] = _re.sub(
                r"projects/[^/]+/",
                f"projects/{project}/",
                base_url,
            )
    return _json.dumps(data, separators=(",", ":"))


def _merge_pi_models_list(
    default_models: list[Any],
    local_models: list[Any],
) -> list[dict[str, Any]]:
    """Union two model lists by id; local overrides default on conflict.

    Accepts ``list[Any]`` because the inputs come from ``json.loads`` and may
    carry non-dict entries; non-dicts and entries without an ``id`` key are
    silently skipped. Preserves ordering: default entries first (with local
    overrides applied in-place), followed by local-only additions.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    local_by_id: dict[str, dict[str, Any]] = {
        cast("dict[str, Any]", m)["id"]: cast("dict[str, Any]", m)
        for m in local_models
        if isinstance(m, dict) and "id" in cast("dict[str, Any]", m)
    }
    for m in default_models:
        if not isinstance(m, dict):
            continue
        d = cast("dict[str, Any]", m)
        if "id" not in d:
            continue
        mid = d["id"]
        out.append(local_by_id.get(mid, d))
        seen.add(mid)
    for m in local_models:
        if not isinstance(m, dict):
            continue
        d = cast("dict[str, Any]", m)
        if "id" not in d:
            continue
        if d["id"] not in seen:
            out.append(d)
            seen.add(d["id"])
    return out


def _merge_pi_provider(
    default_provider: dict[str, Any],
    local_provider: dict[str, Any],
) -> dict[str, Any]:
    """Merge two provider dicts. Local fields win; models are merged by id."""
    merged = dict(default_provider)
    for key, value in local_provider.items():
        if key == "models" and isinstance(value, list):
            merged["models"] = _merge_pi_models_list(
                default_provider.get("models", []) or [],
                value,
            )
        else:
            merged[key] = value
    return merged


def _merge_pi_models_json(
    default_json: dict[str, Any],
    local_json: dict[str, Any],
) -> dict[str, Any]:
    """Merge a packaged + operator models.json document.

    - Top-level scalar keys (``_note``, etc.): local wins when present.
    - ``providers`` is union-merged by name; matching providers go through
      ``_merge_pi_provider`` (local fields win, models merged by id).

    This closes the "stale local file overshadows packaged additions" gap
    that existed while build_pi_models_json picked one source or the other.
    """
    merged: dict[str, Any] = dict(default_json)
    for key, value in local_json.items():
        if key != "providers":
            merged[key] = value

    default_providers = dict(default_json.get("providers", {}) or {})
    local_providers = local_json.get("providers", {}) or {}
    for name, local_provider in local_providers.items():
        if (
            name in default_providers
            and isinstance(default_providers[name], dict)
            and isinstance(local_provider, dict)
        ):
            default_providers[name] = _merge_pi_provider(default_providers[name], local_provider)
        else:
            default_providers[name] = local_provider
    merged["providers"] = default_providers
    return merged


def build_pi_models_json(project: str) -> str:
    """Build pi CLI models.json for cloud execution.

    Starts from the packaged default
    (``metaproc/data/pi-models.default.json``) and overlays any
    operator-authored ``~/.pi/agent/models.json``. The packaged default
    always contributes its providers/models so operators with a stale
    local file still get new providers (e.g. the committed ``openai``
    block) without a manual refresh. Local fields and local-only
    providers/models win on the per-field level.
    apiKey helpers and baseUrl project paths are rewritten for the
    container (see ``_rewrite_pi_models_json``) on the merged result.
    """

    try:
        default_raw = (
            resources.files("metaproc.data").joinpath("pi-models.default.json").read_text()
        )
    except Exception:
        log.warning("Failed to read packaged pi-models.default.json", exc_info=True)
        return ""

    try:
        default_doc = _json.loads(default_raw)
    except Exception:
        log.warning("Failed to parse packaged pi-models.default.json", exc_info=True)
        return ""

    origin = "packaged metaproc/data/pi-models.default.json"
    merged_doc = default_doc
    local_path = Path(_os.path.expanduser("~/.pi/agent/models.json"))
    if local_path.exists():
        try:
            local_doc = _json.loads(local_path.read_text())
            merged_doc = _merge_pi_models_json(default_doc, local_doc)
            origin = f"packaged + overlay {local_path}"
        except Exception:
            log.warning(
                "Failed to merge operator pi models.json at %s; using packaged only",
                local_path,
                exc_info=True,
            )

    try:
        rewritten = _rewrite_pi_models_json(_json.dumps(merged_doc), project)
        log.info("Built pi models.json from %s (%d bytes)", origin, len(rewritten))
        return rewritten
    except Exception:
        log.warning("Failed to rewrite pi models.json", exc_info=True)
        return ""


def build_filestore_mount_script(config: GCPBatchConfig) -> str:
    """Build a host-side Filestore mount script for GCP Batch jobs."""
    if not config.filestore_server:
        return ""

    mount_path = shlex.quote(config.filestore_mount_path)
    remote = shlex.quote(f"{config.filestore_server}:{config.filestore_share}")

    return f"""#!/bin/bash
set -euo pipefail

# GCP Batch VMs use COS or Debian. Install NFS client if not already present.
ensure_nfs_client() {{
  if command -v mount.nfs >/dev/null 2>&1; then
    return
  fi
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y nfs-common
    return
  fi
  echo "Unable to install an NFS client on this Batch VM." >&2
  exit 1
}}

is_mounted() {{
  local target="$1"
  if command -v mountpoint >/dev/null 2>&1; then
    mountpoint -q "$target"
    return
  fi
  grep -qs "[[:space:]]$target[[:space:]]" /proc/mounts
}}

ensure_nfs_client
mkdir -p {mount_path}
if ! is_mounted {mount_path}; then
  mount -t nfs -o vers=3,mountvers=3,proto=tcp {remote} {mount_path}
fi
mkdir -p {mount_path}/runs
"""


def build_filestore_bind_mount(config: GCPBatchConfig) -> str:
    """Build the container bind-mount string for a host-mounted Filestore path."""
    mount_path = config.filestore_mount_path
    return f"{mount_path}:{mount_path}:rw"


def get_container_runs_dir(config: GCPBatchConfig) -> str:
    """Return the RUNS_DIR path jobs should use inside the container.

    For Filestore-backed cloud runs, always prefer the container mount path so
    serialized runtime vars cannot leak a workstation-local RUNS_DIR into
    child commands.
    """
    if config.filestore_server:
        return config.filestore_mount_path + "/runs"
    return config.runs_dir


def apply_container_runs_dir(
    config: GCPBatchConfig,
    variables: dict[str, str],
) -> dict[str, str]:
    """Return a copy of variables with container-side RUNS_DIR normalized."""
    runtime_vars = dict(variables)
    runs_dir = get_container_runs_dir(config)
    if runs_dir:
        runtime_vars["RUNS_DIR"] = runs_dir
    return runtime_vars


def build_batch_runnables(
    config: GCPBatchConfig,
    container_kwargs: dict[str, object],
    env_kwargs: dict[str, object],
) -> list[Any]:
    """Build the runnables list for a Batch job, prepending a Filestore mount if configured.

    Returns a list of Runnable objects: optionally a host-side mount script,
    followed by the container runnable with the given env.
    """
    from google.cloud.batch_v1 import Runnable  # noqa: PLC0415 -- optional [gcp-batch] dependency
    from google.cloud.batch_v1.types import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
        Environment,
    )

    runnables: list[Runnable] = []
    # Copy so we don't mutate the caller's dict when injecting the Filestore
    # bind mount — callers may reuse the same container_kwargs across jobs.
    container_kwargs = dict(container_kwargs)
    if config.filestore_server:
        runnables.append(
            Runnable(
                script=Runnable.Script(text=build_filestore_mount_script(config)),
                display_name="mount-filestore",
            )
        )
        container_kwargs["volumes"] = [build_filestore_bind_mount(config)]

    container = Runnable.Container(**container_kwargs)  # pyright: ignore[reportArgumentType]
    runnables.append(
        Runnable(
            container=container,
            environment=Environment(**env_kwargs),  # pyright: ignore[reportArgumentType]
        )
    )
    return runnables


def is_transient_api_error(exc: Exception) -> bool:
    """Check if a GCP API exception is transient and worth retrying.

    Covers gRPC transport errors, OAuth token-refresh failures, and
    server-side 5xx responses.
    """
    msg = str(exc).lower()
    return any(
        s in msg
        for s in (
            "stream removed",
            "service unavailable",
            "503",
            "504",
            "deadline exceeded",
            "connection reset",
            "connection aborted",
            "transport",
            "broken pipe",
            "eof occurred",
            "token refresh",
            "token_exchange",
            "failed to refresh",
        )
    )


_POLL_RETRY_MAX = 5
_POLL_RETRY_BASE_S = 2.0
_POLL_RETRY_MAX_S = 30.0


async def get_job_with_retry(client: Any, batch_v1: Any, job_name: str) -> Any:
    """Get a Batch job status with retry-and-backoff for transient errors.

    Retries up to ``_POLL_RETRY_MAX`` times on transient GCP API / auth
    transport errors. Non-transient errors propagate immediately.
    """
    for attempt in range(1, _POLL_RETRY_MAX + 1):
        try:
            return await asyncio.to_thread(
                client.get_job,
                batch_v1.GetJobRequest(name=job_name),
            )
        except Exception as exc:
            if not is_transient_api_error(exc) or attempt == _POLL_RETRY_MAX:
                raise
            delay = min(_POLL_RETRY_BASE_S * (2 ** (attempt - 1)), _POLL_RETRY_MAX_S)
            log.warning(
                "Transient error polling %s (attempt %d/%d), retrying in %.0fs: %s",
                job_name,
                attempt,
                _POLL_RETRY_MAX,
                delay,
                exc,
            )
            await asyncio.sleep(delay)
    raise RuntimeError("unreachable")


def sanitize_label(label: str) -> str:
    """Sanitize a label for use in GCP resource names.

    GCP Batch job IDs: lowercase, alphanumeric, hyphens only, max 63 chars.
    """
    return re.sub(r"[^a-z0-9-]", "-", label.lower())[:63].strip("-")


def create_single_task_job(
    *,
    config: GCPBatchConfig,
    env_vars: dict[str, str],
    secret_env_vars: dict[str, str],
    container_command: list[str],
    container_entrypoint: str = "python",
    spot: bool = True,
    job_labels: dict[str, str] | None = None,
) -> Any:
    """Build a single-task GCP Batch ``Job`` (one runnable, no partitioning).

    Mirrors the inline construction in
    :func:`metaproc.cloud.gcp.worker_dispatch._submit_workers` but for the
    one-task gcp-run case. The caller is responsible for submission and
    polling — this returns the unsubmitted ``Job`` object so dry-run
    rendering can serialize it without touching the Batch API.
    """
    from google.cloud.batch_v1.types import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
        AllocationPolicy,
        Job,
        LogsPolicy,
        TaskGroup,
        TaskSpec,
    )

    env_kwargs: dict[str, object] = {"variables": dict(env_vars)}
    if secret_env_vars:
        env_kwargs["secret_variables"] = dict(secret_env_vars)

    runnables = build_batch_runnables(
        config,
        container_kwargs={
            "image_uri": config.container_image,
            "entrypoint": container_entrypoint,
            "commands": list(container_command),
        },
        env_kwargs=env_kwargs,
    )

    task_spec = TaskSpec(
        runnables=runnables,
        max_run_duration=f"{config.max_run_duration_s}s",
        compute_resource=build_compute_resource(config.machine_type),
    )

    task_group = TaskGroup(
        task_spec=task_spec,
        task_count=1,
        parallelism=1,
    )

    instances = AllocationPolicy.InstancePolicyOrTemplate(
        policy=AllocationPolicy.InstancePolicy(
            machine_type=config.machine_type,
            provisioning_model=(
                AllocationPolicy.ProvisioningModel.SPOT
                if spot
                else AllocationPolicy.ProvisioningModel.STANDARD
            ),
            boot_disk=AllocationPolicy.Disk(size_gb=config.boot_disk_gb),
        ),
    )

    network_interfaces: list[AllocationPolicy.NetworkInterface] = []
    if config.network:
        ni = AllocationPolicy.NetworkInterface(network=config.network)
        if config.subnetwork:
            ni.subnetwork = config.subnetwork
        network_interfaces.append(ni)

    network_policy = None
    if network_interfaces:
        network_policy = AllocationPolicy.NetworkPolicy(network_interfaces=network_interfaces)

    allocation_policy = AllocationPolicy(
        instances=[instances],
        network=network_policy,
    )

    if config.service_account_email:
        from google.cloud.batch_v1.types.job import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
            ServiceAccount,
        )

        allocation_policy.service_account = ServiceAccount(email=config.service_account_email)

    labels = dict(job_labels or {})
    labels.setdefault("metaproc-role", "gcp-run")
    allocation_policy.labels = labels

    return Job(
        task_groups=[task_group],
        allocation_policy=allocation_policy,
        logs_policy=LogsPolicy(destination=LogsPolicy.Destination.CLOUD_LOGGING),
        labels=labels,
    )


def build_job_id(*, prefix: str, run_id: str, suffix: str, max_len: int = 63) -> str:
    """Build a valid Batch job id while preserving the uniqueness suffix."""
    prefix_part = sanitize_label(prefix)
    run_part = sanitize_label(run_id)
    suffix_part = sanitize_label(suffix)

    reserved = len(prefix_part) + len(suffix_part) + 2
    available = max_len - reserved
    if available < 0:
        available = 0
    if run_part:
        run_part = run_part[:available].strip("-")

    parts = [prefix_part]
    if run_part:
        parts.append(run_part)
    if suffix_part:
        parts.append(suffix_part)

    job_id = "-".join(parts)[:max_len].strip("-")
    if not job_id:
        return "job"
    if not job_id[0].isalpha():
        job_id = f"a{job_id}"[:max_len].strip("-")
    return job_id
