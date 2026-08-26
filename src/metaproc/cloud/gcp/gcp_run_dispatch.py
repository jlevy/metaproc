"""Dispatch a single-task GCP Batch job for the ``metaproc gcp run`` primitive.

Analogous to ``worker_dispatch.py`` / ``orchestrator_dispatch.py`` but
collapsed to one task. Composes the env (``METAPROC_GCP_RUN_CMD``, the
``METAPROC_WHEEL_GCS`` and ``METAPROC_WHEEL_SHA256`` pair, the
``METAPROC_WORKSPACE_GCS`` and ``METAPROC_WORKSPACE_SHA256`` pair, and
optional ``METAPROC_WORKSPACE_PACKAGES``, and ``RUNS_DIR``), resolves the
``GCP_SECRET_REFS`` registry, builds the Job via
``batch_backend.create_single_task_job``, and submits it via the Batch
client.

``build_gcp_run_job`` is the pure assembly half (no API calls), used by
``--dry-run`` and unit tests. ``dispatch_gcp_run`` is the side-effecting
wrapper that actually submits.
"""

from __future__ import annotations

import json
import logging
import secrets as _secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from metaproc.cloud.gcp.batch_backend import (
    GCPBatchConfig,
    _build_secret_env_vars,
    build_job_id,
    create_single_task_job,
    get_container_runs_dir,
)
from metaproc.io.digests import SHA256_PATTERN

log = logging.getLogger(__name__)

ENTRYPOINT_MODULE = "metaproc.cloud.gcp.gcp_run_entrypoint"

# Env-var names owned by the dispatcher/entrypoint. Callers must not smuggle
# these in via ``--env`` / ``extra_env``; a silent override would break the
# entrypoint's wheel/workspace bootstrap or redirect RUNS_DIR off the
# Filestore mount.
RESERVED_ENV_KEYS: frozenset[str] = frozenset(
    {
        "METAPROC_GCP_RUN_CMD",
        "METAPROC_WHEEL_GCS",
        "METAPROC_WHEEL_SHA256",
        "METAPROC_WORKSPACE_GCS",
        "METAPROC_WORKSPACE_SHA256",
        "METAPROC_WORKSPACE_PACKAGES",
        "RUNS_DIR",
    }
)


@dataclass(frozen=True)
class DispatchGcpRunOptions:
    """Knobs for a single ``metaproc gcp run`` dispatch.

    ``config`` carries the GCP infra (project, region, image, machine,
    secrets, Filestore). The other fields layer on top: artifact URIs,
    extra env/secret vars, optional caller-supplied job name, spot toggle,
    extra labels.
    """

    config: GCPBatchConfig
    wheel_gcs_uri: str = ""
    wheel_sha256: str = ""
    workspace_gcs_uri: str = ""
    workspace_sha256: str = ""
    workspace_packages: tuple[str, ...] = ()
    extra_env: Mapping[str, str] = field(default_factory=dict)
    extra_secrets: Mapping[str, str] = field(default_factory=dict)
    job_name: str = ""
    spot: bool = True
    job_labels: Mapping[str, str] = field(default_factory=dict)


def _generate_job_id() -> str:
    nonce = _secrets.token_hex(3)
    ts = str(int(time.time()))
    return build_job_id(prefix="gcprun", run_id="adhoc", suffix=f"{ts}-{nonce}")


def _get_batch_v1() -> Any:
    """Indirection so tests can stub the Batch client without touching network."""
    from google.cloud import batch_v1  # noqa: PLC0415 -- optional [gcp-batch] dependency

    return batch_v1


def _normalize_workspace_package_paths(package_paths: tuple[str, ...]) -> tuple[str, ...]:
    """Validate paths before serializing them into a comma-delimited env value."""
    normalized: list[str] = []
    for raw_path in package_paths:
        candidate = raw_path.strip()
        parsed = PurePosixPath(candidate)
        if (
            not candidate
            or "," in candidate
            or parsed.is_absolute()
            or ".." in parsed.parts
            or parsed == PurePosixPath(".")
        ):
            raise ValueError(
                "workspace package path must be a non-empty, comma-free "
                f"repository-relative path: {raw_path!r}"
            )
        normalized.append(parsed.as_posix())
    return tuple(normalized)


def validate_gcp_run_secret_service_account(
    config: GCPBatchConfig,
    extra_secrets: Mapping[str, str],
) -> dict[str, str]:
    """Resolve run secrets and require an explicit identity when any are bound.

    GCP Batch otherwise falls back to the project's default Compute Engine service
    account. That implicit identity may not have Secret Manager access and, more
    importantly, is not an operator-selected security boundary.
    """
    secret_env_vars: dict[str, str] = dict(_build_secret_env_vars())
    secret_env_vars.update(extra_secrets)
    if secret_env_vars and not config.service_account_email:
        raise ValueError(
            "Set METAPROC_GCP_SERVICE_ACCOUNT before binding Secret Manager secrets "
            "to a GCP Batch run"
        )
    return secret_env_vars


def build_gcp_run_job(
    cmd: list[str],
    options: DispatchGcpRunOptions,
) -> tuple[str, Any]:
    """Compose a single-task GCP Batch ``Job`` for ``cmd``.

    Pure assembly: no API calls, no upload side effects. Returns
    ``(job_id, job)`` where ``job_id`` is the caller-supplied
    ``options.job_name`` or a generated ``gcprun-…`` id.
    """
    if not cmd:
        raise ValueError("cmd argv must be non-empty")

    workspace_packages = _normalize_workspace_package_paths(options.workspace_packages)

    reserved_conflicts = sorted(set(options.extra_env) & RESERVED_ENV_KEYS)
    if reserved_conflicts:
        raise ValueError(f"extra_env cannot override dispatcher-owned keys: {reserved_conflicts}")

    if bool(options.wheel_gcs_uri) != bool(options.wheel_sha256):
        raise ValueError("wheel_gcs_uri and wheel_sha256 must be provided together")
    if bool(options.workspace_gcs_uri) != bool(options.workspace_sha256):
        raise ValueError("workspace_gcs_uri and workspace_sha256 must be provided together")
    if workspace_packages and not options.workspace_gcs_uri:
        raise ValueError("workspace_packages requires a shipped workspace artifact")
    if options.wheel_sha256 and SHA256_PATTERN.fullmatch(options.wheel_sha256) is None:
        raise ValueError("wheel_sha256 must be exactly 64 hexadecimal characters")
    if options.workspace_sha256 and SHA256_PATTERN.fullmatch(options.workspace_sha256) is None:
        raise ValueError("workspace_sha256 must be exactly 64 hexadecimal characters")

    env_vars: dict[str, str] = {"METAPROC_GCP_RUN_CMD": json.dumps(cmd)}
    if options.wheel_gcs_uri:
        env_vars["METAPROC_WHEEL_GCS"] = options.wheel_gcs_uri
        env_vars["METAPROC_WHEEL_SHA256"] = options.wheel_sha256
    if options.workspace_gcs_uri:
        env_vars["METAPROC_WORKSPACE_GCS"] = options.workspace_gcs_uri
        env_vars["METAPROC_WORKSPACE_SHA256"] = options.workspace_sha256
    if workspace_packages:
        env_vars["METAPROC_WORKSPACE_PACKAGES"] = ",".join(workspace_packages)
    container_runs_dir = get_container_runs_dir(options.config)
    if container_runs_dir:
        env_vars["RUNS_DIR"] = container_runs_dir
    env_vars.update(options.extra_env)

    secret_env_vars = validate_gcp_run_secret_service_account(
        options.config,
        options.extra_secrets,
    )

    labels: dict[str, str] = {"metaproc-role": "gcp-run", "metaproc-dispatch": "gcp-run"}
    labels.update(options.job_labels)

    job = create_single_task_job(
        config=options.config,
        env_vars=env_vars,
        secret_env_vars=secret_env_vars,
        container_command=["-m", ENTRYPOINT_MODULE],
        spot=options.spot,
        job_labels=labels,
    )

    job_id = options.job_name or _generate_job_id()
    return job_id, job


def dispatch_gcp_run(cmd: list[str], options: DispatchGcpRunOptions) -> str:
    """Build and submit a single-task gcp-run Batch job.

    Returns the submitted job's full resource name
    (``projects/<P>/locations/<R>/jobs/<id>``).
    """
    job_id, job = build_gcp_run_job(cmd, options)
    batch_v1 = _get_batch_v1()
    client = batch_v1.BatchServiceClient()
    parent = f"projects/{options.config.project}/locations/{options.config.region}"
    request = batch_v1.CreateJobRequest(parent=parent, job_id=job_id, job=job)
    created = client.create_job(request)
    log.info("Submitted gcp-run job: %s", created.name)
    return created.name
