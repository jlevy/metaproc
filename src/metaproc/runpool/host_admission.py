"""Disk-backed host admission control for local RunPool launches.

Each RunPool still owns its own per-run status, events, and process lifecycle.
This module only gates local subprocess admission across independent RunPool
parents on the same host. The lock primitive is directory creation: `mkdir` is
atomic across processes on normal local filesystems, so no daemon or RPC service
is needed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import psutil
from strif import atomic_output_file

log = logging.getLogger(__name__)

DEFAULT_HOST_ADMISSION_NAMESPACE = "local-agents"
DEFAULT_HOST_ADMISSION_TIMEOUT_S = 300.0
EMPTY_SLOT_GRACE_S = 30.0
UNRECORDED_LEASE_GRACE_S = 120.0
PROCESS_CREATE_TIME_TOLERANCE_S = 1.0


@dataclass(frozen=True)
class HostAdmissionLease:
    """Held host-wide admission slot."""

    namespace: str
    slot_id: int
    slot_dir: Path
    token: str
    label: str
    limit: int
    acquired_at: float

    @property
    def lease_file(self) -> Path:
        return self.slot_dir / "lease.json"


@dataclass(frozen=True)
class HostAdmissionSlotSnapshot:
    """Read-only view of one host admission slot lease."""

    namespace: str
    slot_id: int
    slot_dir: Path
    label: str
    pool_id: str
    limit: int | None
    owner_pid: int | None
    child_pid: int | None
    owner_alive: bool
    child_alive: bool
    age_s: float
    acquired_at: object
    updated_at: object

    @property
    def status(self) -> str:
        """Return ``alive`` when an owner or child process still holds the slot."""
        return "alive" if self.owner_alive or self.child_alive else "stale"

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe representation for CLI output."""
        return {
            "namespace": self.namespace,
            "slot_id": self.slot_id,
            "slot_dir": str(self.slot_dir),
            "label": self.label,
            "pool_id": self.pool_id,
            "limit": self.limit,
            "owner_pid": self.owner_pid,
            "child_pid": self.child_pid,
            "owner_alive": self.owner_alive,
            "child_alive": self.child_alive,
            "status": self.status,
            "age_s": self.age_s,
            "acquired_at": self.acquired_at,
            "updated_at": self.updated_at,
        }


class HostAdmissionGate:
    """A tiny disk-backed semaphore shared by local RunPool processes."""

    def __init__(
        self,
        *,
        root_dir: Path | None,
        namespace: str = DEFAULT_HOST_ADMISSION_NAMESPACE,
        limit: int,
        poll_interval_s: float = 1.0,
        acquire_timeout_s: float | None = DEFAULT_HOST_ADMISSION_TIMEOUT_S,
    ) -> None:
        if limit <= 0:
            raise ValueError("host admission limit must be > 0")
        if poll_interval_s <= 0:
            raise ValueError("host admission poll interval must be > 0")
        if acquire_timeout_s is not None and acquire_timeout_s <= 0:
            raise ValueError("host admission timeout must be > 0 or None")
        self.root_dir: Path = root_dir or Path.home() / ".metaproc" / "runpool" / "host-slots"
        self.namespace: str = _safe_segment(namespace)
        self.limit: int = limit
        self.poll_interval_s: float = poll_interval_s
        self.acquire_timeout_s: float | None = acquire_timeout_s
        self.namespace_dir: Path = self.root_dir / self.namespace

    async def acquire(
        self,
        *,
        label: str,
        pool_id: str,
        metadata: dict[str, object] | None = None,
    ) -> HostAdmissionLease:
        """Wait until a host slot is available and return its lease.

        Raises :class:`TimeoutError` after ``acquire_timeout_s`` so a
        permanently occupied or corrupted namespace cannot stall a run forever.
        Pass ``None`` explicitly to retain unbounded waiting.
        """
        self.namespace_dir.mkdir(parents=True, exist_ok=True)
        deadline = (
            None if self.acquire_timeout_s is None else time.monotonic() + self.acquire_timeout_s
        )
        while True:
            for slot_id in range(self.limit):
                lease = self._try_acquire_slot(
                    slot_id,
                    label=label,
                    pool_id=pool_id,
                    metadata=metadata or {},
                )
                if lease is not None:
                    return lease
            if deadline is not None:
                remaining_s = deadline - time.monotonic()
                if remaining_s <= 0:
                    raise TimeoutError(
                        "timed out waiting for a host admission slot "
                        f"in namespace {self.namespace!r} after "
                        f"{self.acquire_timeout_s:g}s"
                    )
                await asyncio.sleep(min(self.poll_interval_s, remaining_s))
            else:
                await asyncio.sleep(self.poll_interval_s)

    def release(self, lease: HostAdmissionLease) -> None:
        """Release *lease* if this process still owns its token."""
        current = self._read_lease(lease.lease_file)
        if current is None:
            return
        if current.get("token") != lease.token:
            log.warning(
                "Host admission slot token mismatch on release; leaving slot intact: %s",
                lease.slot_dir,
            )
            return
        try:
            shutil.rmtree(lease.slot_dir)
        except FileNotFoundError:
            return
        except OSError:
            log.warning("Failed to release host admission slot %s", lease.slot_dir, exc_info=True)

    def record_child(
        self,
        lease: HostAdmissionLease,
        *,
        child_pid: int | None,
        external_id: str | None,
        backend: str,
    ) -> None:
        """Stamp the launched child identity onto an acquired lease."""
        current = self._read_lease(lease.lease_file)
        if current is None or current.get("token") != lease.token:
            return
        current["child_pid"] = child_pid
        current["child_create_time"] = _process_create_time(child_pid)
        current["external_id"] = external_id
        current["backend"] = backend
        current["updated_at"] = _now()
        self._write_lease(lease.lease_file, current)

    def _try_acquire_slot(
        self,
        slot_id: int,
        *,
        label: str,
        pool_id: str,
        metadata: dict[str, object],
    ) -> HostAdmissionLease | None:
        slot_dir = self.namespace_dir / f"slot-{slot_id}"
        try:
            slot_dir.mkdir()
        except FileExistsError:
            if not self._reclaim_if_stale(slot_dir):
                return None
            try:
                slot_dir.mkdir()
            except FileExistsError:
                return None

        token = uuid.uuid4().hex
        lease = HostAdmissionLease(
            namespace=self.namespace,
            slot_id=slot_id,
            slot_dir=slot_dir,
            token=token,
            label=label,
            limit=self.limit,
            acquired_at=time.time(),
        )
        payload: dict[str, object] = {
            "schema": "metaproc.runpool.host-admission/v1",
            "namespace": self.namespace,
            "slot_id": slot_id,
            "limit": self.limit,
            "token": token,
            "label": label,
            "pool_id": pool_id,
            "owner_pid": os.getpid(),
            "owner_create_time": _process_create_time(os.getpid()),
            "child_pid": None,
            "child_create_time": None,
            "external_id": None,
            "backend": None,
            "acquired_at": _now(),
            "updated_at": _now(),
            "metadata": metadata,
        }
        try:
            self._write_lease(lease.lease_file, payload)
        except Exception:
            _ = _remove_slot(slot_dir)
            raise
        return lease

    def _reclaim_if_stale(self, slot_dir: Path) -> bool:
        lease_file = slot_dir / "lease.json"
        payload = self._read_lease(lease_file)
        if payload is None:
            if _age_s(slot_dir) < EMPTY_SLOT_GRACE_S:
                return False
            return _remove_slot(slot_dir)

        if payload.get("child_pid") is None and _age_s(slot_dir) < UNRECORDED_LEASE_GRACE_S:
            return False

        owner_alive = _process_alive(
            _int_or_none(payload.get("owner_pid")),
            _float_or_none(payload.get("owner_create_time")),
        )
        child_alive = _process_alive(
            _int_or_none(payload.get("child_pid")),
            _float_or_none(payload.get("child_create_time")),
        )
        if owner_alive or child_alive:
            return False
        return _remove_slot(slot_dir)

    def _read_lease(self, lease_file: Path) -> dict[str, object] | None:
        return _read_lease_file(lease_file)

    def _write_lease(self, lease_file: Path, payload: Mapping[str, object]) -> None:
        with atomic_output_file(lease_file, make_parents=True) as tmp:
            _ = Path(tmp).write_text(
                json.dumps(payload, indent=2, sort_keys=True),
                encoding="utf-8",
            )


def _process_create_time(pid: int | None) -> float | None:
    if pid is None:
        return None
    try:
        return float(psutil.Process(pid).create_time())
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def list_host_admission_slots(
    *,
    root_dir: Path | None = None,
    namespace: str = DEFAULT_HOST_ADMISSION_NAMESPACE,
) -> list[HostAdmissionSlotSnapshot]:
    """Return the current disk-backed host admission slot leases.

    This is intentionally read-only. Stale lease reclamation remains in the
    admission gate, so inspection cannot change launch behavior.
    """
    root = root_dir or Path.home() / ".metaproc" / "runpool" / "host-slots"
    safe_namespace = _safe_segment(namespace)
    namespace_dir = root / safe_namespace
    if not namespace_dir.exists():
        return []

    snapshots: list[HostAdmissionSlotSnapshot] = []
    for slot_dir in sorted(namespace_dir.glob("slot-*")):
        if not slot_dir.is_dir():
            continue
        payload = _read_lease_file(slot_dir / "lease.json")
        if payload is None:
            slot_id = _slot_id_from_dir(slot_dir)
            snapshots.append(
                HostAdmissionSlotSnapshot(
                    namespace=safe_namespace,
                    slot_id=slot_id,
                    slot_dir=slot_dir,
                    label="",
                    pool_id="",
                    limit=None,
                    owner_pid=None,
                    child_pid=None,
                    owner_alive=False,
                    child_alive=False,
                    age_s=_age_s(slot_dir),
                    acquired_at=None,
                    updated_at=None,
                )
            )
            continue

        owner_pid = _int_or_none(payload.get("owner_pid"))
        child_pid = _int_or_none(payload.get("child_pid"))
        owner_alive = _process_alive(owner_pid, _float_or_none(payload.get("owner_create_time")))
        child_alive = _process_alive(child_pid, _float_or_none(payload.get("child_create_time")))
        slot_id = _int_or_none(payload.get("slot_id"))
        snapshots.append(
            HostAdmissionSlotSnapshot(
                namespace=str(payload.get("namespace") or safe_namespace),
                slot_id=slot_id if slot_id is not None else _slot_id_from_dir(slot_dir),
                slot_dir=slot_dir,
                label=str(payload.get("label") or ""),
                pool_id=str(payload.get("pool_id") or ""),
                limit=_int_or_none(payload.get("limit")),
                owner_pid=owner_pid,
                child_pid=child_pid,
                owner_alive=owner_alive,
                child_alive=child_alive,
                age_s=_age_s(slot_dir),
                acquired_at=payload.get("acquired_at"),
                updated_at=payload.get("updated_at"),
            )
        )
    return snapshots


def _read_lease_file(lease_file: Path) -> dict[str, object] | None:
    try:
        raw = cast(object, json.loads(lease_file.read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    return {str(key): value for key, value in cast("dict[object, object]", raw).items()}


def _slot_id_from_dir(slot_dir: Path) -> int:
    name = slot_dir.name
    if name.startswith("slot-"):
        parsed = _int_or_none(name.removeprefix("slot-"))
        if parsed is not None:
            return parsed
    return -1


def _process_alive(pid: int | None, create_time: float | None) -> bool:
    if pid is None:
        return False
    try:
        proc = psutil.Process(pid)
        if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
            return False
        if create_time is None:
            return True
        return abs(float(proc.create_time()) - create_time) < PROCESS_CREATE_TIME_TOLERANCE_S
    except psutil.AccessDenied:
        return True
    except psutil.NoSuchProcess:
        return False


def _remove_slot(slot_dir: Path) -> bool:
    try:
        shutil.rmtree(slot_dir)
    except FileNotFoundError:
        return True
    except OSError:
        log.warning("Failed to reclaim stale host admission slot %s", slot_dir, exc_info=True)
        return False
    return True


def _age_s(path: Path) -> float:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return 0.0


def _int_or_none(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if not isinstance(value, str):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if not isinstance(value, str):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _safe_segment(value: str) -> str:
    clean = "".join(c if c.isalnum() or c in "-_." else "-" for c in value.strip())
    clean = clean.strip(".-")
    return clean or DEFAULT_HOST_ADMISSION_NAMESPACE
