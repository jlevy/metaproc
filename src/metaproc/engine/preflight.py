"""Pre-flight checks for run-parallel and cloud execution.

Validates system prerequisites before launching batch work:
disk space, gcloud authentication, adapter connectivity, and
cloud infrastructure readiness (Filestore mount, ADC, CLIs).
"""

from __future__ import annotations

import configparser
import logging
import shutil
import subprocess
from pathlib import Path

from metaproc.config.env_enum import InvalidEnvVar
from metaproc.config.env_vars import MetaprocEnv

log = logging.getLogger(__name__)


def check_disk_space(min_gb: float | None = None, path: str = ".") -> tuple[bool, str]:
    """Check that at least *min_gb* GB of disk space is free.

    When ``min_gb`` is None (the default), reads ``METAPROC_PREFLIGHT_MIN_DISK_GB``
    from the environment, falling back to 5.0 GB. The env override is the
    operator escape hatch when a long-running dispatch genuinely needs to run
    on a near-full disk and the operator has accepted the risk of mid-run
    fill (see e.g. 2026-05-21 large dispatch where the 5 GB default blocked
    tier5 retry while the actual per-batch disk delta was ~300 MB).
    """
    if min_gb is None:
        try:
            min_gb = float(
                MetaprocEnv.METAPROC_PREFLIGHT_MIN_DISK_GB.read_str(default="5.0") or "5.0"
            )
        except (ValueError, AttributeError):
            min_gb = 5.0
    usage = shutil.disk_usage(path)
    free_gb = usage.free / (1024**3)
    if free_gb >= min_gb:
        return True, f"Disk: {free_gb:.1f} GB free (minimum {min_gb:.1f} GB)"
    return False, (
        f"Disk: {free_gb:.1f} GB free — below {min_gb:.1f} GB minimum "
        f"(override via METAPROC_PREFLIGHT_MIN_DISK_GB if you've accepted the risk)"
    )


# Per-item disk footprint observed across a representative batch
# (Mon 6, Tue 17, Wed 28 items × 3 lanes = 51 items × 3 lanes = 153
# item-runs over ~80-100 GB of artifacts). Each item directory averaged
# ~150 MB across the 8 step outputs + per-attempt logs + web-research
# bundle. Conservative budget for pre-flight; operator can tune.
DEFAULT_PER_ITEM_BUDGET_MB = 150


def check_disk_space_for_batch(
    *,
    n_lanes: int,
    n_items: int,
    per_item_mb: int = DEFAULT_PER_ITEM_BUDGET_MB,
    path: str = ".",
    headroom_gb: float = 5.0,
) -> tuple[bool, str]:
    """Pre-flight check sized for a multi-lane, multi-item batch.

    Computes the expected disk budget as
    ``n_lanes × n_items × per_item_mb + headroom_gb`` and refuses to
    launch if the host has less free. Refusal short-circuits the disk-crisis
    class of failures that ate ~3-5h of recovery time in the 2026-05-25
    large batch (3× ENOSPC events). See the disk-budget regression test.

    Operator escape hatch: ``METAPROC_PREFLIGHT_MIN_DISK_GB`` (same env var
    the flat check honors) overrides the computed budget. Use when the
    operator has accepted the risk of mid-run fill — e.g. when a previous
    batch's artifacts will be evicted as this one runs.

    Returns the same ``(passed, message)`` shape as :func:`check_disk_space`
    so call sites can compose them without special casing.
    """
    computed_budget_gb = (n_lanes * n_items * per_item_mb) / 1024.0 + headroom_gb
    override = MetaprocEnv.METAPROC_PREFLIGHT_MIN_DISK_GB.read_str(default="").strip()
    if override:
        try:
            return check_disk_space(min_gb=float(override), path=path)
        except ValueError:
            pass  # fall through to computed budget; the flat check logs the bad value
    usage = shutil.disk_usage(path)
    free_gb = usage.free / (1024**3)
    if free_gb >= computed_budget_gb:
        return True, (
            f"Disk: {free_gb:.1f} GB free (batch budget {computed_budget_gb:.1f} GB"
            f" for {n_lanes} lanes × {n_items} items × {per_item_mb} MB"
            f" + {headroom_gb:.0f} GB headroom)"
        )
    return False, (
        f"Disk: {free_gb:.1f} GB free — below {computed_budget_gb:.1f} GB batch budget"
        f" ({n_lanes} lanes × {n_items} items × {per_item_mb} MB"
        f" + {headroom_gb:.0f} GB headroom)."
        f" Evict old runs under runs/local/ or override via"
        f" METAPROC_PREFLIGHT_MIN_DISK_GB after accepting the risk."
    )


def check_gcloud_auth() -> tuple[bool, str]:
    """Check that a GCP access token can be resolved via google.auth."""
    try:
        from metaproc.cloud.gcp.resolve_token import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
            resolve_gcp_token,  # noqa: PLC0415  # optional GCP extra
        )

        token = resolve_gcp_token()
        return True, f"GCP auth: ok (token len={len(token)})"
    except ImportError:
        return False, "GCP auth: metaproc[gcp] not installed — run `uv sync --extra gcp`"
    except Exception as exc:  # noqa: BLE001
        return False, f"GCP auth: failed — {exc}"


# ── Cloud infrastructure checks ──────────────────────────────────


def check_filestore_mount() -> tuple[bool, str]:
    """Check that Filestore is mounted and writable."""
    server = MetaprocEnv.METAPROC_GCP_FILESTORE_SERVER.read_str(default="")
    if not server:
        return False, "Filestore: METAPROC_GCP_FILESTORE_SERVER not set"
    mount_path = MetaprocEnv.METAPROC_GCP_FILESTORE_MOUNT_PATH.read_str(default="/mnt/filestore")
    p = Path(mount_path)
    if not p.exists():
        return False, f"Filestore: mount path {mount_path} does not exist"
    if not p.is_dir():
        return False, f"Filestore: {mount_path} is not a directory"
    # Check writable by attempting to create a temp file.
    test_file = p / ".preflight-write-test"
    try:
        test_file.write_text("ok")
        test_file.unlink()
    except OSError as exc:
        return False, f"Filestore: {mount_path} is not writable — {exc}"
    return True, f"Filestore: {mount_path} mounted and writable"


def check_adc() -> tuple[bool, str]:
    """Check that Application Default Credentials are available.

    Uses the same ``cloud-platform`` scope that metaproc's own Batch / Storage
    clients request internally. A bare service-account key without an
    explicit scope would otherwise return ``invalid_scope`` on refresh, which
    is not what an operator actually cares about here.
    """
    try:
        from google.auth import (  # noqa: PLC0415 -- guarded import (optional dep / circular)
            default as google_auth_default,
        )
        from google.auth.transport.requests import (  # noqa: PLC0415 -- guarded import (optional dep / circular)
            Request,
        )

        creds, project = google_auth_default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        creds.refresh(Request())
        cred_type = type(creds).__name__
        return True, f"ADC: ok ({cred_type}, project={project})"
    except ImportError:
        return False, "ADC: google-auth not installed — run `uv sync --extra gcp`"
    except Exception as exc:  # noqa: BLE001
        return False, f"ADC: failed — {exc}"


def check_cli(name: str) -> tuple[bool, str]:
    """Check that a CLI tool is available on PATH."""
    path = shutil.which(name)
    if path:
        return True, f"CLI {name}: found at {path}"
    return False, f"CLI {name}: not found on PATH"


def check_gcp_project() -> tuple[bool, str]:
    """Check that METAPROC_GCP_PROJECT is set."""
    project = MetaprocEnv.METAPROC_GCP_PROJECT.read_str(default="")
    if project:
        return True, f"GCP project: {project}"
    return False, "GCP project: METAPROC_GCP_PROJECT not set"


def check_container_image() -> tuple[bool, str]:
    """Check that METAPROC_GCP_CONTAINER_IMAGE is set."""
    image = MetaprocEnv.METAPROC_GCP_CONTAINER_IMAGE.read_str(default="")
    if image:
        return True, f"Container image: {image}"
    return False, "Container image: METAPROC_GCP_CONTAINER_IMAGE not set"


def check_service_account() -> tuple[bool, str]:
    """Report the configured service account, if any.

    A custom service account is optional. When unset, Batch falls back to the
    default compute service account for the VM.
    """
    sa = MetaprocEnv.METAPROC_GCP_SERVICE_ACCOUNT.read_str(default="")
    if sa:
        return True, f"Service account: {sa}"
    return True, "Service account: unset (using default compute service account)"


def check_dispatch_resources() -> tuple[bool, str]:
    """Validate the integer dispatch knobs parse cleanly.

    ``METAPROC_GCP_BOOT_DISK_GB`` and ``METAPROC_GCP_MAX_RUN_DURATION_S`` are
    consumed at submit time via ``read_int``; an unparsable value raises only
    once dispatch is mid-flight. ``METAPROC_GCP_TASK_CPU_MILLI`` and
    ``METAPROC_GCP_TASK_MEMORY_MIB`` are still string-typed in the registry but
    are ``int()``-cast at submit (``batch_backend.resolve_compute_resource``);
    a malformed value would raise an opaque ``ValueError`` mid-build. Catching
    all four here turns those into a single fail-fast preflight failure with
    the offending var name and value.
    """
    failures: list[str] = []
    try:
        MetaprocEnv.METAPROC_GCP_BOOT_DISK_GB.read_int(default=50)
    except InvalidEnvVar as exc:
        failures.append(str(exc))
    try:
        MetaprocEnv.METAPROC_GCP_MAX_RUN_DURATION_S.read_int(default=28800)
    except InvalidEnvVar as exc:
        failures.append(str(exc))
    cpu = MetaprocEnv.METAPROC_GCP_TASK_CPU_MILLI.read_str(default="").strip()
    if cpu:
        try:
            int(cpu)
        except ValueError:
            failures.append(f"METAPROC_GCP_TASK_CPU_MILLI={cpu!r} is not a valid integer")
    mem = MetaprocEnv.METAPROC_GCP_TASK_MEMORY_MIB.read_str(default="").strip()
    if mem:
        try:
            int(mem)
        except ValueError:
            failures.append(f"METAPROC_GCP_TASK_MEMORY_MIB={mem!r} is not a valid integer")
    if failures:
        return False, "Dispatch resources: " + "; ".join(failures)
    return True, "Dispatch resources: boot-disk / max-duration / CPU / memory parse cleanly"


def check_machine_type() -> tuple[bool, str]:
    """Report the GCE machine type used for ``--backend gcp-worker`` fan-out.

    The submit path falls back to the backend default when unset, so this is a
    report-only check — it surfaces drift between operator intent and what the
    Batch job will actually request without blocking dispatch.
    """
    mt = MetaprocEnv.METAPROC_GCP_MACHINE_TYPE.read_str(default="")
    if mt:
        return True, f"Machine type: {mt}"
    return True, "Machine type: unset (using backend default)"


# ── Composite runners ────────────────────────────────────────────


def run_preflight(*, needs_gcloud: bool = False) -> list[tuple[bool, str]]:
    """Run all pre-flight checks. Returns list of (passed, message) tuples."""
    results: list[tuple[bool, str]] = []
    results.append(check_disk_space())
    if needs_gcloud:
        results.append(check_gcloud_auth())
    return results


def check_filestore_config() -> tuple[bool, str]:
    """Check that Filestore env vars are set (does not require a local mount).

    The local dispatcher needs METAPROC_GCP_FILESTORE_SERVER to configure NFS
    volumes on the remote VMs.  The actual mount is only needed on the VMs
    themselves — use ``check_filestore_mount()`` for that.
    """
    server = MetaprocEnv.METAPROC_GCP_FILESTORE_SERVER.read_str(default="")
    if not server:
        return False, "Filestore config: METAPROC_GCP_FILESTORE_SERVER not set"
    return True, f"Filestore config: server={server}"


def run_cloud_preflight() -> list[tuple[bool, str]]:
    """Run cloud-specific pre-flight checks for --cloud dispatch from a local machine.

    Validates that the env vars and credentials needed to *submit* a cloud job
    are present.  Does **not** require Filestore to be locally mounted — only
    the remote VMs need the mount.
    """
    results: list[tuple[bool, str]] = []
    results.append(check_gcp_project())
    results.append(check_service_account())
    results.append(check_container_image())
    results.append(check_machine_type())
    results.append(check_dispatch_resources())
    results.append(check_adc())
    results.append(check_filestore_config())
    results.append(check_cli("gcloud"))
    return results


# ── Cloud dispatch warnings (non-fatal) ────────────────────────────


def _git(args: list[str], *, cwd: Path) -> str | None:
    """Run a git command and return stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _metaproc_source_paths(root: Path) -> tuple[str, ...]:
    """Return consumer paths that represent the Metaproc source checkout."""
    paths = ["metaproc"]
    gitmodules = root / ".gitmodules"
    if not gitmodules.is_file():
        return tuple(paths)

    config = configparser.ConfigParser(interpolation=None)
    config.read_string(gitmodules.read_text())

    for section in config.sections():
        if not section.startswith("submodule "):
            continue
        path = config.get(section, "path", fallback="").strip()
        url = config.get(section, "url", fallback="").rstrip("/")
        repository_name = url.rsplit("/", maxsplit=1)[-1].removesuffix(".git")
        if path and (Path(path).name == "metaproc" or repository_name == "metaproc"):
            paths.append(path)
    return tuple(dict.fromkeys(paths))


def check_metaproc_wheel_for_branch_edits(
    *, repo_root: Path | None = None, base_ref: str = "origin/main"
) -> tuple[bool, str]:
    """Warn when the tracked branch has Metaproc source edits but no wheel override.

    Metaproc is baked into the agent image at build time, so Batch
    workers don't consume tracked-branch source changes unless the
    operator sets ``METAPROC_WHEEL_GCS`` so ``container_bootstrap`` reinstalls
    metaproc from the uploaded wheel. ``METAPROC_WORKSPACE_GCS`` is **not**
    a valid substitute: the workspace installs only the configured companion
    packages; ``metaproc`` itself stays image-baked unless a wheel is shipped.

    Returns ``(True, …)`` when ``METAPROC_WHEEL_GCS`` is set, or when the
    tracked branch carries no ``metaproc/`` changes vs. ``origin/main``.

    Returns ``(False, <warning>)`` in all other branches, including the
    cases where we cannot be sure (not a git repo, base ref not fetched) —
    the operator should always see those states, not have them silently
    downgraded.
    """
    wheel_gcs = MetaprocEnv.METAPROC_WHEEL_GCS.read_str(default="").strip()
    if wheel_gcs:
        return True, "Metaproc artifact: METAPROC_WHEEL_GCS set — tracked branch changes will ship"

    root = repo_root or Path.cwd()
    if _git(["rev-parse", "--is-inside-work-tree"], cwd=root) != "true":
        return (
            False,
            (
                f"Metaproc artifact: {root} is not a git repo — cannot verify whether this "
                "dispatch will ship current-branch metaproc/ code. If you're on a branch "
                "with metaproc/ changes, set METAPROC_WHEEL_GCS or the workers will run "
                "the image-baked metaproc."
            ),
        )

    if _git(["rev-parse", "--verify", base_ref], cwd=root) is None:
        return (
            False,
            (
                f"Metaproc artifact: base ref {base_ref} is not fetched — cannot "
                "compare the tracked branch against it. `git fetch origin main` and "
                "retry, or set METAPROC_WHEEL_GCS explicitly."
            ),
        )

    try:
        source_paths = _metaproc_source_paths(root)
    except (OSError, configparser.Error) as exc:
        return (
            False,
            (
                "Metaproc artifact: cannot inspect .gitmodules to locate the source "
                f"checkout ({exc}). Fix .gitmodules or set METAPROC_WHEEL_GCS explicitly."
            ),
        )
    source_pathspecs = [f":(literal){path}" for path in source_paths]

    # Committed Metaproc source changes ahead of the base ref.
    ahead = _git(
        ["rev-list", "--count", f"{base_ref}..HEAD", "--", *source_pathspecs],
        cwd=root,
    )
    ahead_count = int(ahead) if ahead and ahead.isdigit() else 0

    # Uncommitted Metaproc source changes in the working tree or index.
    dirty = _git(["status", "--porcelain", "--", *source_pathspecs], cwd=root)
    dirty_count = len([ln for ln in (dirty or "").splitlines() if ln.strip()])

    if ahead_count == 0 and dirty_count == 0:
        return True, f"Metaproc artifact: no tracked changes in source vs. {base_ref}"

    pieces: list[str] = []
    if ahead_count:
        pieces.append(f"{ahead_count} commit(s) ahead of {base_ref}")
    if dirty_count:
        pieces.append(f"{dirty_count} uncommitted file(s)")
    detail = " + ".join(pieces)
    return (
        False,
        (
            "Metaproc artifact: tracked branch has "
            f"{detail} in Metaproc source but METAPROC_WHEEL_GCS is not set — Batch "
            "will run the image-baked metaproc code, not this branch's. Build a "
            "wheel from the Metaproc source checkout, upload it to gs://, "
            "and `export METAPROC_WHEEL_GCS=gs://…`. METAPROC_WORKSPACE_GCS does "
            "NOT cover metaproc itself — only configured companion packages."
        ),
    )


def run_cloud_preflight_warnings(*, repo_root: Path | None = None) -> list[tuple[bool, str]]:
    """Run non-fatal cloud-dispatch warnings.

    Unlike :func:`run_cloud_preflight`, failures here are *warnings* — the
    dispatch should still proceed, but the operator should see them so they
    can decide whether to cancel before a Batch job starts.
    """
    results: list[tuple[bool, str]] = []
    results.append(check_metaproc_wheel_for_branch_edits(repo_root=repo_root))
    return results
