"""Wheel + workspace artifact helpers for ``metaproc gcp run`` dispatch.

Two responsibilities:

1. Build a metaproc wheel from the local source tree (``build_wheel``)
   and upload it to GCS (``upload_wheel_to_gcs``).
2. Package the current repo working tree into a tar.gz
   (``package_workspace``) and upload it to GCS
   (``upload_workspace_to_gcs``).

Both artifact types ship to ``gs://<bucket>/<prefix>/...`` so a Batch task
container can fetch the current-branch metaproc + workspace without
requiring an agent-image rebuild or a cross-VPC git clone.

The wheel build path was extracted from
``metaproc/src/metaproc/commands/gcp.py:_run_self_install`` so the
self-install SSH command and the gcp-run dispatcher share one
implementation.
"""

from __future__ import annotations

import logging
import subprocess
import tarfile
import tempfile
from pathlib import Path

from google.cloud import storage

from metaproc.io.digests import file_sha256

log = logging.getLogger(__name__)

DEFAULT_DISPATCH_BUCKET = ""
DEFAULT_GCS_PREFIX = "gcp-run"

# Fixed asset name for the packaged workspace tarball. Every dispatch
# step (build, upload, container-side fetch, dry-run URI) imports this
# rather than spelling out the literal.
WORKSPACE_TARBALL_NAME: str = "workspace.tar.gz"


def find_metaproc_source_dir(start: Path | None = None) -> Path:
    """Locate the metaproc source root (the dir whose ``pyproject.toml`` lives next to ``src/metaproc/``)."""
    p = (start or Path(__file__)).resolve()
    while p != p.parent:
        if (p / "pyproject.toml").exists() and p.name == "metaproc":
            return p
        p = p.parent
    raise RuntimeError("Could not find metaproc source directory")


def find_repo_root(start: Path | None = None) -> Path:
    """Locate the git repo root (first ancestor containing a ``.git`` entry)."""
    p = (start or Path.cwd()).resolve()
    while p != p.parent:
        if (p / ".git").exists():
            return p
        p = p.parent
    raise RuntimeError(f"Could not find git repo root from {start or Path.cwd()}")


def build_wheel(*, source_dir: Path | None = None, out_dir: Path | None = None) -> Path:
    """Build a metaproc wheel via ``uv build --wheel``.

    Returns the path to the single ``.whl`` produced. Raises ``RuntimeError``
    on build failure or if the wheel count is not exactly one.
    """
    src = source_dir or find_metaproc_source_dir()
    out = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="metaproc-wheel-"))
    out.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["uv", "build", "--wheel", "-o", str(out), str(src)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to build wheel:\n{result.stderr}")
    wheels = sorted(out.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"Expected exactly one wheel in {out}, got {len(wheels)}: {wheels}")
    log.info("Built wheel: %s", wheels[0])
    return wheels[0]


def _contain_in_repo(repo: Path, rel: str) -> str:
    """Resolve ``rel`` against ``repo`` and reject paths that escape it.

    Returns the normalised repo-relative path. Rejects absolute paths and
    anything that resolves outside ``repo`` — otherwise a caller could
    smuggle ``../../etc/secrets`` into the workspace tarball via
    ``--sync`` or ``--sync-only``.
    """
    if Path(rel).is_absolute():
        raise ValueError(f"Path must be repo-relative, got absolute: {rel!r}")
    full = (repo / rel).resolve()
    if not full.is_relative_to(repo):
        raise ValueError(f"Path escapes repo root ({repo}): {rel!r}")
    return str(full.relative_to(repo))


def package_workspace(
    *,
    repo_root: Path,
    extra_paths: list[str] | None = None,
    sync_only: list[str] | None = None,
    exclude_prefixes: tuple[str, ...] = ("metaproc/",),
    out_path: Path | None = None,
) -> Path:
    """Tar+gzip a subset of the repo working tree for shipment to a Batch task.

    Default path-set: tracked files (``git ls-files``) unioned with
    untracked-but-not-gitignored files (``git ls-files --others
    --exclude-standard``), minus anything under ``exclude_prefixes``
    (default: ``metaproc/``, since the wheel ships that separately), plus
    any caller-supplied ``extra_paths``.

    Including untracked-non-ignored files matters because iterating on a
    new spec or dataset file that hasn't been committed yet would
    otherwise silently ship stale data to the Batch task.

    ``sync_only`` overrides the default entirely; only the listed paths
    are packaged.

    All ``extra_paths`` and ``sync_only`` entries must resolve inside
    ``repo_root``; absolute paths or ``..`` escapes raise ``ValueError``.

    Missing paths log a warning and are skipped. Returns the path to the
    created tarball.
    """
    repo = repo_root.resolve()
    if sync_only is not None:
        paths = [_contain_in_repo(repo, p) for p in sync_only]
    else:
        tracked = subprocess.run(
            ["git", "ls-files"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=True,
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=True,
        )
        seen: set[str] = set()
        all_files: list[str] = []
        for line in tracked.stdout.splitlines() + untracked.stdout.splitlines():
            if line and line not in seen:
                seen.add(line)
                all_files.append(line)
        paths = [
            p
            for p in all_files
            if not any(p == pref.rstrip("/") or p.startswith(pref) for pref in exclude_prefixes)
        ]
        if extra_paths:
            paths.extend(_contain_in_repo(repo, p) for p in extra_paths)

    if out_path is None:
        out_path = Path(tempfile.mkdtemp(prefix="metaproc-workspace-")) / WORKSPACE_TARBALL_NAME
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tarfile.open(out_path, "w:gz") as tar:
        for rel in paths:
            full = repo / rel
            if not full.exists():
                log.warning("Skipping %s — not present in working tree", rel)
                continue
            tar.add(str(full), arcname=rel, recursive=True)
    log.info("Packaged %d entries into %s", len(paths), out_path)
    return out_path


def upload_to_gcs(local_path: Path, gs_uri: str) -> str:
    """Upload a local file to a ``gs://`` URI. Returns the URI on success."""
    if not gs_uri.startswith("gs://"):
        raise ValueError(f"Expected gs:// URI, got {gs_uri!r}")
    bucket_name, _, blob_path = gs_uri[len("gs://") :].partition("/")
    if not blob_path:
        raise ValueError(f"Missing blob path in gs:// URI: {gs_uri!r}")
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_path)
    blob.metadata = {"metaproc-sha256": file_sha256(local_path)}
    blob.upload_from_filename(str(local_path))
    log.info("Uploaded %s -> %s", local_path, gs_uri)
    return gs_uri


def upload_wheel_to_gcs(
    wheel: Path,
    *,
    bucket: str,
    job_id: str,
    prefix: str = DEFAULT_GCS_PREFIX,
) -> str:
    """Upload a built wheel to ``gs://<bucket>/<prefix>/<job_id>/<wheel-name>``.

    Scoping by ``job_id`` keeps two concurrent dispatches with the same wheel
    version (common when re-running from the same commit/branch) from
    overwriting each other's install target — a queued Batch task must
    install the wheel that matched its own workspace tarball.
    """
    gs_uri = f"gs://{bucket}/{prefix}/{job_id}/{wheel.name}"
    return upload_to_gcs(wheel, gs_uri)


def upload_workspace_to_gcs(
    workspace: Path,
    *,
    bucket: str,
    job_id: str,
    prefix: str = DEFAULT_GCS_PREFIX,
) -> str:
    """Upload a workspace tarball to ``gs://<bucket>/<prefix>/<job_id>/<WORKSPACE_TARBALL_NAME>``."""
    gs_uri = f"gs://{bucket}/{prefix}/{job_id}/{WORKSPACE_TARBALL_NAME}"
    return upload_to_gcs(workspace, gs_uri)
