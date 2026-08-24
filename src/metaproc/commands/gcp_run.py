"""``metaproc gcp run`` — dispatch arbitrary commands to a single GCP Batch task.

See ``docs/arch/arch-metaproc-core.md``
for the full design. This module owns:

- argv parsing for the ``gcp run`` Typer subcommand,
- artifact build/upload (wheel + workspace tarball) gated by ``--no-wheel`` /
  ``--no-workspace``,
- ``GCPBatchConfig`` assembly from CLI flags + env vars,
- dispatch via :func:`gcp_run_dispatch.dispatch_gcp_run` (or dry-run rendering).

Blocking mode (default) tails Cloud Logging for the job and exits with a
unix code derived from the terminal Batch state via
:func:`gcp_run_logs.tail_gcp_run_logs`. ``--detach`` skips the tail and
prints ``job_name`` + a console log URL.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import typer
from google.protobuf.json_format import MessageToDict

from metaproc.cloud.gcp.batch_backend import GCPBatchConfig
from metaproc.cloud.gcp.dispatch_artifacts import (
    DEFAULT_DISPATCH_BUCKET,
    DEFAULT_GCS_PREFIX,
    WORKSPACE_TARBALL_NAME,
    build_wheel,
    find_repo_root,
    package_workspace,
    upload_wheel_to_gcs,
    upload_workspace_to_gcs,
)
from metaproc.cloud.gcp.gcp_run_dispatch import (
    RESERVED_ENV_KEYS,
    DispatchGcpRunOptions,
    _generate_job_id,
    _normalize_workspace_package_paths,
    build_gcp_run_job,
    dispatch_gcp_run,
)
from metaproc.cloud.gcp.gcp_run_logs import build_log_url, tail_gcp_run_logs
from metaproc.config.env_vars import MetaprocEnv
from metaproc.io.digests import file_sha256

log = logging.getLogger(__name__)

DEFAULT_MACHINE_TYPE = "e2-standard-4"
DEFAULT_TIMEOUT_S = 3600
DEFAULT_RUNS_DIR = "/mnt/filestore/runs"


def _parse_kv_pairs(items: list[str] | None, flag_name: str) -> dict[str, str]:
    """Parse a list of ``KEY=VALUE`` strings into a dict.

    Fails hard on entries missing ``=`` — silent skip would surprise the
    caller when the value never reaches the task.
    """
    out: dict[str, str] = {}
    for raw in items or []:
        if "=" not in raw:
            raise typer.BadParameter(
                f"{flag_name} expects KEY=VALUE, got {raw!r}",
                param_hint=flag_name,
            )
        key, _, value = raw.partition("=")
        if not key:
            raise typer.BadParameter(
                f"{flag_name} entry has empty key: {raw!r}",
                param_hint=flag_name,
            )
        out[key] = value
    return out


def _expand_secret_ref(value: str, project: str) -> str:
    """Expand a shorthand secret reference to a full Secret Manager resource name.

    Accepts three input shapes:

    - ``projects/<p>/secrets/<s>/versions/<v>`` — used as-is (full ref).
    - ``<secret>`` — expands to ``projects/<project>/secrets/<secret>/versions/latest``.
    - ``<secret>:<version>`` — expands to ``projects/<project>/secrets/<secret>/versions/<version>``.

    The shorthand mirrors ``gcloud secrets versions access`` UX so callers
    don't need to paste the full resource path for the common case.
    """
    if value.startswith("projects/"):
        return value
    if ":" in value:
        secret, _, version = value.partition(":")
    else:
        secret, version = value, "latest"
    if not secret:
        raise typer.BadParameter(
            f"--secret value missing secret name: {value!r}", param_hint="--secret"
        )
    return f"projects/{project}/secrets/{secret}/versions/{version}"


def _validate_workspace_package_sources(
    repo_root: Path,
    package_paths: tuple[str, ...],
    sync_only: list[str] | None,
) -> None:
    """Fail before artifact work when a requested package cannot be shipped."""
    resolved_repo = repo_root.resolve()
    shipped_roots: tuple[Path, ...] = ()
    if sync_only is not None:
        roots: list[Path] = []
        for raw_path in sync_only:
            candidate = Path(raw_path)
            resolved = (resolved_repo / candidate).resolve()
            if candidate.is_absolute() or not resolved.is_relative_to(resolved_repo):
                raise typer.BadParameter(
                    f"--sync-only path must be repository-relative: {raw_path!r}",
                    param_hint="--sync-only",
                )
            roots.append(resolved)
        shipped_roots = tuple(roots)

    for package_path in package_paths:
        package_dir = (resolved_repo / package_path).resolve()
        if not package_dir.is_relative_to(resolved_repo):
            raise typer.BadParameter(
                f"workspace package resolves outside the repository: {package_path!r}",
                param_hint="--workspace-package",
            )
        pyproject = package_dir / "pyproject.toml"
        if not pyproject.is_file():
            raise typer.BadParameter(
                f"workspace package must contain pyproject.toml: {package_path!r}",
                param_hint="--workspace-package",
            )
        if shipped_roots and not any(
            package_dir == root or package_dir.is_relative_to(root) for root in shipped_roots
        ):
            raise typer.BadParameter(
                f"--sync-only does not ship workspace package {package_path!r}",
                param_hint="--sync-only",
            )


def _build_config(
    *,
    image: str,
    machine_type: str,
    timeout: int,
    spot: bool,
    runs_dir: str,
    no_filestore: bool,
) -> GCPBatchConfig:
    """Build a :class:`GCPBatchConfig` from CLI flags + env defaults.

    ``METAPROC_GCP_PROJECT`` is required. The container image comes from
    ``--image`` or ``METAPROC_GCP_CONTAINER_IMAGE``. Filestore is enabled
    iff ``--no-filestore`` is unset and ``METAPROC_GCP_FILESTORE_SERVER``
    is in the env.
    """
    project = MetaprocEnv.METAPROC_GCP_PROJECT.read_str(default="")
    if not project:
        raise typer.BadParameter(
            "METAPROC_GCP_PROJECT env var is required (or run `gcloud config set project`)",
            param_hint="METAPROC_GCP_PROJECT",
        )

    effective_image = image or MetaprocEnv.METAPROC_GCP_CONTAINER_IMAGE.read_str(default="")
    if not effective_image:
        raise typer.BadParameter(
            "--image required or set METAPROC_GCP_CONTAINER_IMAGE",
            param_hint="--image",
        )

    filestore_server = ""
    if not no_filestore:
        filestore_server = MetaprocEnv.METAPROC_GCP_FILESTORE_SERVER.read_str(default="")

    return GCPBatchConfig(
        project=project,
        region=MetaprocEnv.METAPROC_GCP_REGION.read_str(default="us-central1"),
        container_image=effective_image,
        machine_type=machine_type,
        spot=spot,
        max_run_duration_s=timeout,
        service_account_email=MetaprocEnv.METAPROC_GCP_SERVICE_ACCOUNT.read_str(default=""),
        network=MetaprocEnv.METAPROC_GCP_NETWORK.read_str(default=""),
        subnetwork=MetaprocEnv.METAPROC_GCP_SUBNETWORK.read_str(default=""),
        runs_dir=runs_dir if not no_filestore else "",
        filestore_server=filestore_server,
        filestore_share=MetaprocEnv.METAPROC_GCP_FILESTORE_SHARE.read_str(default="/metaproc_runs"),
        filestore_mount_path=MetaprocEnv.METAPROC_GCP_FILESTORE_MOUNT_PATH.read_str(
            default="/mnt/filestore"
        ),
    )


def _ship_artifacts(
    *,
    no_wheel: bool,
    no_workspace: bool,
    sync: list[str] | None,
    sync_only: list[str] | None,
    job_id: str,
    bucket: str,
    prefix: str,
) -> tuple[str, str, str, str]:
    """Build + upload the wheel and workspace tarball as configured.

    Returns wheel URI/digest followed by workspace URI/digest. Each pair is
    empty when the corresponding ``--no-*`` flag is set.
    """
    wheel_uri = ""
    wheel_sha256 = ""
    if not no_wheel:
        wheel = build_wheel()
        wheel_sha256 = file_sha256(wheel)
        wheel_uri = upload_wheel_to_gcs(wheel, bucket=bucket, job_id=job_id, prefix=prefix)

    workspace_uri = ""
    workspace_sha256 = ""
    if not no_workspace:
        workspace = package_workspace(
            repo_root=find_repo_root(),
            extra_paths=list(sync) if sync else None,
            sync_only=list(sync_only) if sync_only else None,
        )
        workspace_sha256 = file_sha256(workspace)
        workspace_uri = upload_workspace_to_gcs(
            workspace, bucket=bucket, job_id=job_id, prefix=prefix
        )

    return wheel_uri, wheel_sha256, workspace_uri, workspace_sha256


def run_command(
    cmd: list[str] = typer.Argument(  # noqa: B008
        ...,
        help="Command and args to run on a GCP Batch task (use -- to separate from flags).",
    ),
    no_wheel: bool = typer.Option(
        False, "--no-wheel", help="Skip metaproc wheel build/upload (use the image-baked metaproc)."
    ),
    no_workspace: bool = typer.Option(
        False, "--no-workspace", help="Skip workspace tarball; only the wheel ships."
    ),
    sync: list[str] = typer.Option(  # noqa: B008
        None, "--sync", help="Additional repo-relative path to include in the workspace tarball."
    ),
    sync_only: list[str] = typer.Option(  # noqa: B008
        None,
        "--sync-only",
        help="Ship ONLY these paths instead of the default full-tree-minus-metaproc set.",
    ),
    workspace_package: list[str] = typer.Option(  # noqa: B008
        None,
        "--workspace-package",
        help=(
            "Repo-relative Python package to install editable from the shipped workspace. "
            "Repeatable; keeps nested uv commands on the image environment."
        ),
    ),
    machine_type: str = typer.Option(
        DEFAULT_MACHINE_TYPE, "--machine-type", help="GCE machine type for the task VM."
    ),
    timeout: int = typer.Option(
        DEFAULT_TIMEOUT_S, "--timeout", help="Max run duration in seconds."
    ),
    spot: bool = typer.Option(
        True, "--spot/--no-spot", help="Use Spot provisioning (cheaper, preemptible)."
    ),
    image: str = typer.Option(
        "", "--image", help="Agent container image (default: $METAPROC_GCP_CONTAINER_IMAGE)."
    ),
    runs_dir: str = typer.Option(
        DEFAULT_RUNS_DIR, "--runs-dir", help="RUNS_DIR inside the container."
    ),
    no_filestore: bool = typer.Option(
        False, "--no-filestore", help="Skip the Filestore NFS mount."
    ),
    env: list[str] = typer.Option(  # noqa: B008
        None, "--env", help="K=V plaintext env var on the task. Repeatable."
    ),
    secret: list[str] = typer.Option(  # noqa: B008
        None,
        "--secret",
        help=(
            "K=REF Secret Manager binding. REF may be a full "
            "``projects/P/secrets/S/versions/V`` path, bare ``S`` (→ versions/latest), "
            "or ``S:V``. Repeatable."
        ),
    ),
    detach: bool = typer.Option(
        False,
        "--detach",
        help="Submit and exit immediately; print job name + log URL instead of tailing.",
    ),
    job_name: str = typer.Option(
        "", "--job-name", help="Override generated job name (default: gcprun-<ts>-<nonce>)."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the resolved Batch job spec as JSON and exit 0."
    ),
    bucket: str = typer.Option(
        DEFAULT_DISPATCH_BUCKET,
        "--dispatch-bucket",
        envvar="METAPROC_GCS_BUCKET",
        help="Required GCS bucket for wheel + workspace artifacts.",
    ),
) -> None:
    """Dispatch ``cmd`` to a single GCP Batch task with current metaproc + repo.

    Default behaviour ships a fresh-built wheel from the local source tree
    plus a tarball of the current repo working tree, mounts Filestore at
    ``/mnt/filestore`` (with ``RUNS_DIR=/mnt/filestore/runs``), resolves
    ``GCP_SECRET_REFS`` (``GH_TOKEN``, ``CLAUDE_CODE_CREDS_JSON``), and
    ``execvp``'s ``cmd`` inside the container.
    """
    config = _build_config(
        image=image,
        machine_type=machine_type,
        timeout=timeout,
        spot=spot,
        runs_dir=runs_dir,
        no_filestore=no_filestore,
    )

    extra_env = _parse_kv_pairs(env, "--env")
    reserved = sorted(set(extra_env) & RESERVED_ENV_KEYS)
    if reserved:
        raise typer.BadParameter(
            f"--env cannot set reserved keys owned by the dispatcher: {reserved}",
            param_hint="--env",
        )
    raw_secrets = _parse_kv_pairs(secret, "--secret")
    extra_secrets = {
        key: _expand_secret_ref(val, config.project) for key, val in raw_secrets.items()
    }

    if no_workspace and workspace_package:
        raise typer.BadParameter(
            "--workspace-package requires workspace shipping",
            param_hint="--workspace-package",
        )

    try:
        workspace_packages = _normalize_workspace_package_paths(tuple(workspace_package or ()))
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--workspace-package") from exc
    if workspace_packages:
        _validate_workspace_package_sources(
            find_repo_root(),
            workspace_packages,
            list(sync_only) if sync_only else None,
        )

    if not dry_run and (not no_wheel or not no_workspace) and not bucket:
        raise typer.BadParameter(
            "--dispatch-bucket or METAPROC_GCS_BUCKET is required when shipping artifacts",
            param_hint="--dispatch-bucket",
        )

    # The artifact path needs a job_id up-front so workspace tarballs land
    # under the right gs://…/<job_id>/ prefix. Use the override if set or
    # the same generator the dispatcher uses, then pass it through as
    # options.job_name so dispatch doesn't generate a different one.
    job_id = job_name or _generate_job_id()

    # Skip artifact build/upload for --dry-run — the spec just needs
    # placeholder URIs so the env-var shape is visible. Running `uv build`
    # and pushing a workspace tarball to GCS for a spec print is wasteful
    # and requires GCS creds the dry-run caller may not have.
    if dry_run:
        wheel_uri = "" if no_wheel else "gs://<dry-run>/wheel.whl"
        wheel_sha256 = "" if no_wheel else "0" * 64
        workspace_uri = "" if no_workspace else f"gs://<dry-run>/{WORKSPACE_TARBALL_NAME}"
        workspace_sha256 = "" if no_workspace else "0" * 64
    else:
        wheel_uri, wheel_sha256, workspace_uri, workspace_sha256 = _ship_artifacts(
            no_wheel=no_wheel,
            no_workspace=no_workspace,
            sync=sync,
            sync_only=sync_only,
            job_id=job_id,
            bucket=bucket,
            prefix=DEFAULT_GCS_PREFIX,
        )

    options = DispatchGcpRunOptions(
        config=config,
        wheel_gcs_uri=wheel_uri,
        wheel_sha256=wheel_sha256,
        workspace_gcs_uri=workspace_uri,
        workspace_sha256=workspace_sha256,
        workspace_packages=workspace_packages,
        extra_env=extra_env,
        extra_secrets=extra_secrets,
        job_name=job_id,
        spot=spot,
    )

    if dry_run:
        _, job = build_gcp_run_job(list(cmd), options)
        spec = MessageToDict(job._pb, preserving_proto_field_name=True)  # noqa: SLF001
        typer.echo(json.dumps({"job_id": job_id, "job": spec}, indent=2, sort_keys=True))
        return

    job_resource_name = dispatch_gcp_run(list(cmd), options)
    typer.echo(job_resource_name)

    if detach:
        typer.echo(build_log_url(job_resource_name))
        return

    rc = tail_gcp_run_logs(
        job_resource_name=job_resource_name,
        project=config.project,
    )
    raise typer.Exit(code=rc)
