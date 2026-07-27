"""Orchestrator lease — cross-host safety for concurrent orchestrators.

Prevents two orchestrators from operating on the same run simultaneously.
The lease file at ``{run_dir}/.state/orchestrator-lease.yaml`` records the
current owner, and a heartbeat thread updates ``last_heartbeat_at``
periodically.

A second orchestrator refuses to start if a live lease exists. A stale
lease (heartbeat expired, or owner PID dead) can be taken over explicitly.
"""

from __future__ import annotations

import contextlib
import logging
import os
import platform
import secrets
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

from frontmatter_format import read_yaml_file, to_yaml_string
from strif import atomic_output_file

from metaproc.errors import CLIError
from metaproc.io.mkdir_lock import (
    MkdirLockLease,
    MkdirLockTimeoutError,
    acquire_mkdir_lock,
    release_mkdir_lock,
)
from metaproc.paths import ORCHESTRATOR_LEASE_FILE, STATE_DIR

log = logging.getLogger(__name__)

# Heartbeat interval and staleness threshold.
HEARTBEAT_INTERVAL_S = 30
STALE_THRESHOLD_S = 120  # 4x heartbeat — generous for NFS latency
LEASE_LOCK_TIMEOUT_S = 5.0
LEASE_LOCK_RETRY_S = 0.05
LEASE_LOCK_STALE_S = 30.0

_owned_lease_tokens: dict[Path, str] = {}
_owned_lease_tokens_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def _lease_path(run_dir: Path) -> Path:
    return run_dir / STATE_DIR / ORCHESTRATOR_LEASE_FILE


def _parse_pid(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _lease_lock_path(run_dir: Path) -> Path:
    return _lease_path(run_dir).with_suffix(".lock")


def _lease_lock_owner_path(lock_path: Path) -> Path:
    return lock_path.with_name(f"{lock_path.name}.owner.yaml")


def _remember_owned_lease(path: Path, owner_token: str) -> None:
    with _owned_lease_tokens_lock:
        _owned_lease_tokens[path] = owner_token


def _owned_lease_token(path: Path) -> str:
    with _owned_lease_tokens_lock:
        return _owned_lease_tokens.get(path, "")


def _forget_owned_lease(path: Path) -> None:
    with _owned_lease_tokens_lock:
        _owned_lease_tokens.pop(path, None)


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is alive on this host."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # POSIX reports EPERM when the process exists but this user cannot
        # signal it. Treat it as live so we never break an active lease.
        return True


def _is_lease_stale(lease: dict[str, object]) -> bool:
    """Check if a lease has expired based on heartbeat timestamp."""
    last_hb = lease.get("last_heartbeat_at", "")
    if not last_hb:
        return True
    try:
        hb_time = _parse_iso(str(last_hb))
    except (ValueError, TypeError):
        return True
    age = (datetime.now(tz=UTC) - hb_time).total_seconds()
    return age > STALE_THRESHOLD_S


def is_orchestrator_alive(run_dir: Path) -> bool:
    """Return True if ``run_dir`` has an orchestrator lease with a fresh heartbeat.

    Reads ``run_dir/.state/orchestrator-lease.yaml`` and checks
    ``last_heartbeat_at`` against :data:`STALE_THRESHOLD_S`. For same-host
    leases, also verifies that the owner PID still exists. A missing,
    unreadable, stale, or dead-owner lease returns False.

    Intended as a lightweight liveness probe for consumers outside the
    lease-acquire path (e.g. ``metaproc status``), which must distinguish
    "the orchestrator is still working" from "all fan-out items reached a
    terminal state but the DAG has more steps to run."
    """
    path = _lease_path(run_dir)
    if not path.exists():
        return False
    data = _read_yaml_mapping(path)
    if data is None:
        return False
    if _is_lease_stale(data):
        return False

    owner_host = data.get("owner_host", "")
    owner_pid = _parse_pid(data.get("owner_pid", 0))
    if owner_host == platform.node() and owner_pid:
        return _is_pid_alive(owner_pid)
    return True


def clear_orchestrator_lease(run_dir: Path, *, owner_token: str | None = None) -> bool:
    """Remove the orchestrator lease if it still matches ``owner_token``.

    This is intentionally narrower than ``release_lease()``: it is used by
    operator tooling after terminating an orchestrator process that cannot run
    its own ``finally`` block. If ``owner_token`` is supplied and the lease has
    changed owners, the file is left alone.
    """
    path = _lease_path(run_dir)
    with _LeaseWriteLock(run_dir):
        raw = _read_yaml_mapping(path)
        if raw is None:
            return False
        if owner_token is not None and raw.get("owner_token") != owner_token:
            return False
        path.unlink()
    return True


def _can_take_over(lease: dict[str, object]) -> tuple[bool, str]:
    """Determine if the current process can take over a lease.

    Returns (can_take_over, reason).
    """
    if _is_lease_stale(lease):
        return True, "heartbeat expired"

    owner_host = lease.get("owner_host", "")
    owner_pid = _parse_pid(lease.get("owner_pid", 0))

    # Same host — check PID liveness.
    if owner_host == platform.node() and owner_pid and not _is_pid_alive(owner_pid):
        return True, f"owner PID {owner_pid} is dead on this host"

    # Different host — can only rely on heartbeat staleness (already checked).
    return False, "lease is live"


def _read_yaml_mapping(path: Path) -> dict[str, object] | None:
    try:
        raw = read_yaml_file(path)
    except FileNotFoundError:
        return None
    except Exception:
        log.debug("Could not read YAML mapping at %s", path, exc_info=True)
        return None
    return raw if isinstance(raw, dict) else None


def _lock_age_s(lock_path: Path) -> float:
    return time.time() - lock_path.stat().st_mtime


def _lease_lock_is_stale(lock_path: Path) -> bool:
    lock_data = _read_yaml_mapping(_lease_lock_owner_path(lock_path))
    if lock_data is None:
        return _lock_age_s(lock_path) > LEASE_LOCK_TIMEOUT_S

    owner_host = str(lock_data.get("owner_host", ""))
    owner_pid = lock_data.get("owner_pid", 0)
    if owner_host == platform.node() and owner_pid:
        parsed_pid = _parse_pid(owner_pid)
        if parsed_pid is None:
            return True
        return not _is_pid_alive(parsed_pid)

    return _lock_age_s(lock_path) > LEASE_LOCK_STALE_S


def _write_lease_lock_owner(path: Path, data: dict[str, object]) -> None:
    with atomic_output_file(path, make_parents=True) as tmp:
        Path(tmp).write_text(to_yaml_string(data), encoding="utf-8")


class _LeaseWriteLock:
    """Serialize lease mutations across hosts using the shared mkdir lock."""

    def __init__(self, run_dir: Path) -> None:
        self._path = _lease_lock_path(run_dir)
        self._owner_path = _lease_lock_owner_path(self._path)
        self._token = secrets.token_hex(8)
        self._lease: MkdirLockLease | None = None

    def __enter__(self) -> Self:
        try:
            self._lease = acquire_mkdir_lock(
                self._path,
                timeout=LEASE_LOCK_TIMEOUT_S,
                poll_interval=LEASE_LOCK_RETRY_S,
                is_stale=_lease_lock_is_stale,
            )
        except MkdirLockTimeoutError as exc:
            msg = f"Timed out waiting for lease lock: {self._path}"
            raise CLIError(msg) from exc
        try:
            _write_lease_lock_owner(
                self._owner_path,
                {
                    "owner_host": platform.node(),
                    "owner_pid": os.getpid(),
                    "owner_token": self._token,
                    "acquired_at": _now_iso(),
                },
            )
        except Exception:
            release_mkdir_lock(self._path)
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        if self._lease is None:
            return
        current = _read_yaml_mapping(self._owner_path)
        if current is None or current.get("owner_token") != self._token:
            return
        with contextlib.suppress(FileNotFoundError):
            self._owner_path.unlink()
        try:
            release_mkdir_lock(self._path)
        except OSError:
            log.warning(
                "Failed to release orchestrator lease mutation lock %s; stale-after will reclaim it",
                self._path,
                exc_info=True,
            )


def _owns_current_lease(path: Path, lease: dict[str, object]) -> bool:
    owner_token = _owned_lease_token(path)
    return bool(owner_token) and lease.get("owner_token") == owner_token


def acquire_lease(
    run_dir: Path,
    *,
    owner_type: str = "local",
    command_summary: str = "",
    force: bool = False,
) -> Path:
    """Acquire the orchestrator lease for a run directory.

    Raises CLIError if another orchestrator holds a live lease.
    Set ``force=True`` to take over regardless.
    """
    path = _lease_path(run_dir)
    state_dir = run_dir / STATE_DIR
    state_dir.mkdir(parents=True, exist_ok=True)

    owner_token = secrets.token_hex(16)
    with _LeaseWriteLock(run_dir):
        raw = _read_yaml_mapping(path)
        if raw is not None and not force:
            can_take, reason = _can_take_over(raw)
            if not can_take:
                owner_desc = (
                    f"{raw.get('owner_type', '?')} on {raw.get('owner_host', '?')} "
                    f"(PID {raw.get('owner_pid', '?')})"
                )
                raise CLIError(
                    f"Another orchestrator holds the lease: {owner_desc}. "
                    f"Last heartbeat: {raw.get('last_heartbeat_at', 'unknown')}. "
                    f"Use --force to take over."
                )
            log.info("Taking over stale lease (%s)", reason)

        now = _now_iso()
        data: dict[str, object] = {
            "owner_type": owner_type,
            "owner_host": platform.node(),
            "owner_pid": os.getpid(),
            "owner_token": owner_token,
            "started_at": now,
            "last_heartbeat_at": now,
            "command_summary": command_summary,
        }

        with atomic_output_file(path) as tmp:
            Path(tmp).write_text(to_yaml_string(data), encoding="utf-8")

    _remember_owned_lease(path, owner_token)

    log.info("Acquired orchestrator lease: %s (PID %d)", path, os.getpid())
    return path


def update_heartbeat(run_dir: Path) -> None:
    """Update the heartbeat timestamp in the lease file."""
    path = _lease_path(run_dir)
    with _LeaseWriteLock(run_dir):
        raw = _read_yaml_mapping(path)
        if raw is None or not _owns_current_lease(path, raw):
            return

        raw["last_heartbeat_at"] = _now_iso()
        with atomic_output_file(path) as tmp:
            Path(tmp).write_text(to_yaml_string(raw), encoding="utf-8")


def release_lease(run_dir: Path) -> None:
    """Remove the lease file when the orchestrator finishes."""
    path = _lease_path(run_dir)
    try:
        with _LeaseWriteLock(run_dir):
            raw = _read_yaml_mapping(path)
            if raw is None or not _owns_current_lease(path, raw):
                return
            path.unlink()
            log.info("Released orchestrator lease: %s", path)
    finally:
        _forget_owned_lease(path)


class LeaseHeartbeat:
    """Background thread that updates the lease heartbeat periodically.

    Usage::

        with LeaseHeartbeat(run_dir):
            # ... orchestration work ...
    """

    def __init__(self, run_dir: Path, interval: int = HEARTBEAT_INTERVAL_S) -> None:
        self._run_dir = run_dir
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name="lease-heartbeat",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    @property
    def is_running(self) -> bool:
        """Return whether the heartbeat thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                update_heartbeat(self._run_dir)
            except Exception:
                log.exception("Failed to update lease heartbeat")

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
