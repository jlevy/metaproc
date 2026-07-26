"""Shared container bootstrap for GCP Batch entrypoints.

Both the worker entrypoint (``worker_entrypoint.py``) and orchestrator
entrypoint (``orchestrator_entrypoint.py``) need the same setup:

1. Process-scoped Git authentication with ambient helpers disabled
2. Resolve a shipped, cloned, or image-bundled workspace
3. Install explicitly configured workspace packages for plugin discovery
4. Run installed consumer bootstrap hooks
5. Ensure ``RUNS_DIR`` exists
6. Write pi CLI models config if ``METAPROC_PI_MODELS_JSON`` is set

This module provides a single ``bootstrap_container()`` function that
performs all of the above and returns a ``BootstrapResult`` with the
resolved work directory.

When ``METAPROC_WHEEL_GCS`` and/or ``METAPROC_WORKSPACE_GCS`` are present,
the standard worker/orchestrator bootstrap can also consume the same shipped
artifacts used by ``metaproc gcp run``. This lets the normal
``run-process --cloud`` path pick up current-branch ``metaproc/`` changes
without requiring an image rebuild.

For the lighter-weight ``metaproc gcp run`` entrypoint, ``bootstrap_gcp_run``
provides a minimal install-wheel + extract-workspace flow.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, entry_points, version
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from metaproc.config.env_vars import MetaprocEnv
from metaproc.dispatch.repo_sync_payload import RepoSyncPayload
from metaproc.io.digests import verify_file_sha256
from metaproc.io.safe_archive import safe_extract_tar

log = logging.getLogger(__name__)

WORK_DIR = "/workspace/repo"
FALLBACK_WORK_DIR = "/tmp/metaproc/repo"
# gcp-run entrypoint workspace + wheel scratch paths.
GCP_RUN_WORKSPACE_DIR = "/workspace"
GCP_RUN_WORKSPACE_ARCHIVE_DIR = "/tmp/metaproc-workspace"
GCP_RUN_WHEEL_DIR = "/tmp/metaproc-wheel"


@dataclass
class BootstrapResult:
    """Result of container bootstrap."""

    work_dir: str
    """Path to the cloned repository (or empty workspace)."""


@dataclass(frozen=True)
class BootstrapContext:
    """Context passed to installed consumer bootstrap hooks."""

    work_dir: str
    git_token: str = ""


def bootstrap_container() -> BootstrapResult:
    """Common GCP Batch container setup.

    Reads configuration from environment variables:

    - ``METAPROC_RUN_BRANCH`` / ``METAPROC_REPO_URL`` -- repo to clone
    - ``GH_TOKEN`` -- optional GitHub token consumed by temporary Git askpass
    - ``RUNS_DIR`` -- base path for run artifacts (Filestore NFS mount)
    - ``METAPROC_PI_MODELS_JSON`` -- pi CLI models config JSON
    """
    gh_token = MetaprocEnv.GH_TOKEN.read_str(default="")
    os.environ.pop(MetaprocEnv.GH_TOKEN.name, None)
    runs_dir = MetaprocEnv.RUNS_DIR.read_str(default="")
    pi_models_json = MetaprocEnv.METAPROC_PI_MODELS_JSON.read_str(default="")

    repo_sync = RepoSyncPayload.from_env()
    repo_url = repo_sync.repo_url
    run_branch = repo_sync.run_branch
    wheel_gcs = repo_sync.wheel_gcs
    wheel_sha256 = repo_sync.wheel_sha256
    workspace_gcs = repo_sync.workspace_gcs
    workspace_sha256 = repo_sync.workspace_sha256
    sparse_paths = _parse_relative_paths(repo_sync.sparse_paths)
    workspace_packages = _parse_relative_paths(repo_sync.workspace_packages)
    bundled_repo_dir = repo_sync.bundled_repo_dir

    # Point uv at the baked venv; otherwise `uv run` ignores VIRTUAL_ENV, re-resolves
    # against the workspace pyproject.toml, and fails because `metaproc/` is not on
    # disk after a sparse clone.
    os.environ.setdefault("UV_PROJECT_ENVIRONMENT", "/opt/venv")
    os.environ.setdefault("UV_NO_SYNC", "1")

    if wheel_gcs:
        _install_wheel_from_gcs(wheel_gcs, wheel_sha256)

    # Emit a single source-of-truth line so post-mortems are unambiguous about
    # which delivery path provided the metaproc code running in this container.
    _log_metaproc_source(wheel_gcs=wheel_gcs, workspace_gcs=workspace_gcs)

    work_dir = WORK_DIR

    if workspace_gcs:
        work_dir = _extract_workspace_from_gcs(workspace_gcs, workspace_sha256)
        _install_workspace_packages(work_dir, workspace_packages, "workspace tarball")
    elif run_branch and repo_url:
        try:
            work_dir = _bootstrap_sparse_clone(
                run_branch,
                repo_url,
                ensure_writable_dir(
                    WORK_DIR,
                    fallback=FALLBACK_WORK_DIR,
                    purpose="work directory",
                ),
                gh_token=gh_token,
                sparse_paths=sparse_paths,
            )
            _install_workspace_packages(work_dir, workspace_packages, "repository clone")
        except RuntimeError as exc:
            bundled_dir = _prepare_bundled_repo(bundled_repo_dir)
            if bundled_dir is None:
                raise
            log.warning("Repository clone failed (%s); falling back to bundled workspace", exc)
            work_dir = bundled_dir
            _install_workspace_packages(work_dir, workspace_packages, "bundled image")
    else:
        bundled_dir = _prepare_bundled_repo(bundled_repo_dir)
        if bundled_dir is not None:
            work_dir = bundled_dir
            _install_workspace_packages(work_dir, workspace_packages, "bundled image")
        else:
            work_dir = ensure_writable_dir(
                WORK_DIR,
                fallback=FALLBACK_WORK_DIR,
                purpose="work directory",
            )

    _run_consumer_bootstrap_hooks(BootstrapContext(work_dir=work_dir, git_token=gh_token))

    # Ensure RUNS_DIR exists.
    if runs_dir:
        runs_path = runs_dir if os.path.isabs(runs_dir) else os.path.join(work_dir, runs_dir)
        os.makedirs(runs_path, exist_ok=True)
        log.info("RUNS_DIR=%s (resolved to %s)", runs_dir, runs_path)

    # Write pi CLI models config if provided.
    if pi_models_json:
        pi_config_dir = os.path.expanduser("~/.pi/agent")
        os.makedirs(pi_config_dir, exist_ok=True)
        models_path = os.path.join(pi_config_dir, "models.json")
        with open(models_path, "w") as f:
            f.write(pi_models_json)
        log.info("Wrote pi models config to %s (%d bytes)", models_path, len(pi_models_json))

    return BootstrapResult(work_dir=work_dir)


def _log_metaproc_source(*, wheel_gcs: str, workspace_gcs: str) -> None:
    """Emit a single line identifying which metaproc delivery path is live.

    There are exactly two metaproc delivery paths:

    - ``wheel_gcs`` — ``METAPROC_WHEEL_GCS`` was set and the wheel was
      force-reinstalled over the baked ``/opt/venv`` earlier in bootstrap.
    - ``image`` — fall through: metaproc is whatever was baked into the
      agent image at build time.

    ``METAPROC_WORKSPACE_GCS`` is logged separately because a workspace may
    supply consumer packages while Metaproc itself still comes from the image.
    """
    if wheel_gcs:
        source = "wheel_gcs"
        uri = wheel_gcs
    else:
        source = "image"
        uri = ""

    try:
        mp_version = version("metaproc")
    except PackageNotFoundError:
        mp_version = "unknown"

    if uri:
        log.info(
            "container_bootstrap: metaproc source=%s version=%s uri=%s", source, mp_version, uri
        )
    else:
        log.info("container_bootstrap: metaproc source=%s version=%s", source, mp_version)

    if workspace_gcs:
        log.info(
            "container_bootstrap: workspace_gcs=%s (consumer workspace; metaproc source unchanged)",
            workspace_gcs,
        )


def _bootstrap_sparse_clone(
    run_branch: str,
    repo_url: str,
    work_dir: str,
    *,
    gh_token: str = "",
    sparse_paths: tuple[str, ...] = (),
) -> str:
    """Clone the requested branch, optionally using sparse checkout paths."""
    log.info("Cloning branch %s from %s into %s", run_branch, repo_url, work_dir)

    # Initialize repo + sparse checkout config before fetching any objects.
    rc = _run(["git", "init", work_dir])
    if rc != 0:
        raise RuntimeError(f"git init failed (exit code {rc})")
    rc = _run(["git", "remote", "add", "origin", repo_url], cwd=work_dir)
    if rc != 0:
        raise RuntimeError(f"git remote add failed (exit code {rc})")
    if sparse_paths:
        rc = _run(["git", "sparse-checkout", "init", "--cone"], cwd=work_dir)
        if rc != 0:
            raise RuntimeError(f"git sparse-checkout init failed (exit code {rc})")
        rc = _run(["git", "sparse-checkout", "set", *sparse_paths], cwd=work_dir)
        if rc != 0:
            raise RuntimeError(f"git sparse-checkout set failed (exit code {rc})")

    # Fetch only the single branch at depth 1 and check out.
    with ephemeral_git_credentials(gh_token) as git_env:
        rc = _run(
            ["git", "fetch", "--depth", "1", "origin", run_branch],
            cwd=work_dir,
            env=git_env,
        )
    if rc != 0:
        raise RuntimeError(f"git fetch failed (exit code {rc})")
    rc = _run(["git", "checkout", f"origin/{run_branch}"], cwd=work_dir)
    if rc != 0:
        raise RuntimeError(f"git checkout failed (exit code {rc})")

    return work_dir


def _prepare_bundled_repo(bundled_repo_dir: str) -> str | None:
    """Use an explicitly configured workspace baked into the image."""
    if not bundled_repo_dir or not os.path.isdir(bundled_repo_dir):
        return None
    log.info("Using bundled workspace from %s", bundled_repo_dir)
    return bundled_repo_dir


def _install_workspace_packages(
    repo_dir: str,
    package_paths: tuple[str, ...],
    source: str,
) -> None:
    """Install explicitly configured workspace packages for plugin discovery."""
    for relative in package_paths:
        package_dir = os.path.join(repo_dir, relative)
        if not os.path.isdir(package_dir):
            raise RuntimeError(f"configured workspace package is missing: {relative}")
        _strip_uv_sources(os.path.join(package_dir, "pyproject.toml"))
        log.info("Installing workspace package %s from %s", relative, source)
        rc = _run(["uv", "pip", "install", "--no-deps", "-e", package_dir])
        if rc != 0:
            raise RuntimeError(
                f"failed to install workspace package {relative!r} from {source} (rc={rc})"
            )


def _strip_uv_sources(pyproject_path: str) -> None:
    """Remove [tool.uv.sources] section from pyproject.toml.

    This section can contain workspace references (e.g. ``metaproc = { workspace = true }``)
    that cause uv to fail when the package is installed outside the monorepo workspace.
    """
    if not os.path.isfile(pyproject_path):
        return
    with open(pyproject_path) as f:
        lines = f.readlines()
    out: list[str] = []
    skip = False
    for line in lines:
        if line.strip() == "[tool.uv.sources]":
            skip = True
            continue
        if skip and (line.startswith("[") or line.strip() == ""):
            if line.strip() == "":
                continue
            skip = False
        if not skip:
            out.append(line)
    with open(pyproject_path, "w") as f:
        f.writelines(out)
    log.info("Stripped [tool.uv.sources] from %s", pyproject_path)


def ensure_writable_dir(path: str, *, fallback: str, purpose: str) -> str:
    """Return a writable directory, falling back when the preferred path fails."""
    try:
        os.makedirs(path, exist_ok=True)
        return path
    except OSError as exc:
        log.warning(
            "Preferred %s %s is not writable (%s); falling back to %s",
            purpose,
            path,
            exc,
            fallback,
        )
        os.makedirs(fallback, exist_ok=True)
        return fallback


def _parse_relative_paths(raw: str) -> tuple[str, ...]:
    """Parse and validate a comma-separated list of repository-relative paths."""
    paths: list[str] = []
    for value in raw.split(","):
        candidate = value.strip()
        if not candidate:
            continue
        parsed = PurePosixPath(candidate)
        if parsed.is_absolute() or ".." in parsed.parts or parsed == PurePosixPath("."):
            raise RuntimeError(f"workspace path must be repository-relative: {candidate!r}")
        paths.append(parsed.as_posix())
    return tuple(paths)


def _run_consumer_bootstrap_hooks(context: BootstrapContext) -> None:
    """Load and run installed consumer bootstrap entry points."""
    for entry_point in entry_points(group="metaproc.container_bootstrap"):
        hook = entry_point.load()
        if not callable(hook):
            raise RuntimeError(
                f"consumer bootstrap entry point {entry_point.name!r} is not callable"
            )
        log.info("Running consumer bootstrap hook %s", entry_point.name)
        hook(context)


@contextmanager
def ephemeral_git_credentials(token: str) -> Generator[Mapping[str, str]]:
    """Expose a Git token only through a temporary askpass helper."""
    base_env = dict(os.environ)
    base_env.pop(MetaprocEnv.GH_TOKEN.name, None)
    base_env.update(
        {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": "",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    if not token:
        yield base_env
        return

    with TemporaryDirectory(prefix="metaproc-git-auth-") as temp_dir:
        askpass = Path(temp_dir) / "askpass.sh"
        askpass.write_text(
            "#!/bin/sh\n"
            'case "$1" in\n'
            '  *Username*) printf "%s\\n" "x-access-token" ;;\n'
            '  *Password*) printf "%s\\n" "$METAPROC_GIT_TOKEN" ;;\n'
            "  *) exit 1 ;;\n"
            "esac\n"
        )
        askpass.chmod(0o700)
        git_env = dict(base_env)
        git_env.update(
            {
                "GIT_ASKPASS": str(askpass),
                "GIT_ASKPASS_REQUIRE": "force",
                "GIT_TERMINAL_PROMPT": "0",
                "METAPROC_GIT_TOKEN": token,
            }
        )
        yield git_env


def run_command(
    cmd: list[str],
    cwd: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> int:
    """Run a command and return its exit code."""
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env)
        return proc.returncode
    except Exception:
        executable = Path(cmd[0]).name if cmd else "<empty>"
        log.error(
            "Failed to run %s command (%d argument(s)); details redacted", executable, len(cmd)
        )
        return 1


# Compatibility aliases for code that imported the pre-extraction private helpers.
_ensure_writable_dir = ensure_writable_dir
_ephemeral_git_credentials = ephemeral_git_credentials
_run = run_command


def bootstrap_gcp_run(*, home: Path, env: Mapping[str, str]) -> str:
    """Bootstrap a single ``metaproc gcp run`` task container.

    Two optional steps, each driven by an env var:

    1. ``METAPROC_WHEEL_GCS`` — gs:// URI to the current-branch metaproc
       wheel. Downloaded via the ``google-cloud-storage`` Python client
       and force-reinstalled into the baked ``/opt/venv`` (which already
       has the ``[gcp-batch]`` extras pre-installed by the Dockerfile).
       The freshly installed wheel takes
       priority over any image-baked metaproc.
    2. ``METAPROC_WORKSPACE_GCS`` — gs:// URI to a workspace tar.gz.
       Downloaded and extracted into ``GCP_RUN_WORKSPACE_DIR``
       (``/workspace``) so the user's command sees the current-branch
       repo files.

    Returns the path the user command should chdir into
    (``/workspace`` if a workspace was extracted, else ``/tmp``). The
    ``home`` argument is reserved for future per-user state (e.g.
    ``~/.config`` materialization); the gcp-run entrypoint already calls
    each adapter's own ``bootstrap(home)`` for credential blobs.
    """
    wheel_gcs = env.get("METAPROC_WHEEL_GCS", "").strip()
    wheel_sha256 = env.get("METAPROC_WHEEL_SHA256", "").strip()
    workspace_gcs = env.get("METAPROC_WORKSPACE_GCS", "").strip()
    workspace_sha256 = env.get("METAPROC_WORKSPACE_SHA256", "").strip()
    _ = home

    if wheel_gcs:
        _install_wheel_from_gcs(wheel_gcs, wheel_sha256)

    work_dir = "/tmp"
    if workspace_gcs:
        work_dir = _extract_workspace_from_gcs(workspace_gcs, workspace_sha256)

    return work_dir


def _download_from_gcs(gs_uri: str, local_path: str) -> None:
    """Download a single GCS object to ``local_path`` via the Python client."""
    from google.cloud import (  # noqa: PLC0415 -- optional [gcp-batch] dependency; type: ignore[attr-defined]
        storage,
    )

    bucket_name, _, blob_path = gs_uri[len("gs://") :].partition("/")
    if not bucket_name or not blob_path:
        raise RuntimeError(f"Malformed gs:// URI: {gs_uri!r}")
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_path)
    blob.download_to_filename(local_path)


def _install_wheel_from_gcs(wheel_gcs: str, expected_sha256: str) -> None:
    """Download a wheel from GCS and force-reinstall into the baked ``/opt/venv``.

    ``uv tool install`` creates an isolated per-tool venv that ignores the
    ``[gcp-batch]`` extras the agent image pre-installs into ``/opt/venv``
    — the resulting CLI crashes at import time on ``google.cloud.batch_v1``.
    We reuse the baked venv instead and let the wheel's own deps satisfy
    everything else.
    """
    if not wheel_gcs.startswith("gs://"):
        raise RuntimeError(f"METAPROC_WHEEL_GCS must be a gs:// URI, got {wheel_gcs!r}")
    os.makedirs(GCP_RUN_WHEEL_DIR, exist_ok=True)
    wheel_name = wheel_gcs.rsplit("/", 1)[-1]
    if not wheel_name.endswith(".whl"):
        raise RuntimeError(f"Expected wheel name in {wheel_gcs!r}, got {wheel_name!r}")
    local = os.path.join(GCP_RUN_WHEEL_DIR, wheel_name)
    local_path = Path(local)
    if not expected_sha256:
        raise RuntimeError("METAPROC_WHEEL_SHA256 is required for downloaded artifact verification")
    try:
        try:
            _download_from_gcs(wheel_gcs, local)
        except Exception as exc:
            raise RuntimeError(f"download {wheel_gcs} failed: {exc}") from exc
        verified_sha256 = verify_file_sha256(
            local_path, expected_sha256, metadata_name="METAPROC_WHEEL_SHA256"
        )
        rc = _run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                "/opt/venv/bin/python",
                "--force-reinstall",
                f"{local}[gcp-batch]",
            ]
        )
        if rc != 0:
            raise RuntimeError(
                f"uv pip install --force-reinstall {local}[gcp-batch] failed (exit code {rc})"
            )
    finally:
        local_path.unlink(missing_ok=True)
    log.info(
        "Installed metaproc wheel from %s into /opt/venv (sha256=%s)",
        wheel_gcs,
        verified_sha256,
    )


def _extract_workspace_from_gcs(workspace_gcs: str, expected_sha256: str) -> str:
    """Download a workspace tar.gz from GCS and extract it into ``/workspace``."""
    from metaproc.cloud.gcp.dispatch_artifacts import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
        WORKSPACE_TARBALL_NAME,
    )

    if not workspace_gcs.startswith("gs://"):
        raise RuntimeError(f"METAPROC_WORKSPACE_GCS must be a gs:// URI, got {workspace_gcs!r}")
    os.makedirs(GCP_RUN_WORKSPACE_DIR, exist_ok=True)
    os.makedirs(GCP_RUN_WORKSPACE_ARCHIVE_DIR, exist_ok=True)
    local_tar = os.path.join(GCP_RUN_WORKSPACE_ARCHIVE_DIR, WORKSPACE_TARBALL_NAME)
    if not expected_sha256:
        raise RuntimeError(
            "METAPROC_WORKSPACE_SHA256 is required for downloaded artifact verification"
        )
    local_path = Path(local_tar)
    try:
        try:
            _download_from_gcs(workspace_gcs, local_tar)
        except Exception as exc:
            raise RuntimeError(f"download {workspace_gcs} failed: {exc}") from exc
        verified_sha256 = verify_file_sha256(
            local_path, expected_sha256, metadata_name="METAPROC_WORKSPACE_SHA256"
        )
        safe_extract_tar(local_path, Path(GCP_RUN_WORKSPACE_DIR))
    finally:
        local_path.unlink(missing_ok=True)
    log.info(
        "Extracted workspace from %s into %s (sha256=%s)",
        workspace_gcs,
        GCP_RUN_WORKSPACE_DIR,
        verified_sha256,
    )
    return GCP_RUN_WORKSPACE_DIR
