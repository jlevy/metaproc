"""Append-only JSONL event log for the run pool.

Records every state transition: process start/exit/kill, concurrency
adjustments, pressure readings, and pool lifecycle events.  Each line
is a self-contained JSON object.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Self

from metaproc.settings import POOL_INITIAL_CONCURRENCY

log = logging.getLogger(__name__)


class EventLogger:
    """Append-only JSONL writer for pool events.

    Events are flushed immediately after each write to survive crashes.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: IO[str] | None = None

    def open(self) -> None:
        """Open the event log for appending."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, "a")  # noqa: SIM115

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    @property
    def path(self) -> Path:
        return self._path

    def _write(self, event: dict[str, object]) -> None:
        if self._file is None:
            log.warning("EventLogger.write() called before open(); dropping event")
            return
        event["ts"] = datetime.now(UTC).isoformat(timespec="seconds")
        self._file.write(json.dumps(event, default=str) + "\n")
        self._file.flush()

    def pool_start(
        self,
        pool_id: str,
        backend: str,
        max_concurrency: int,
        initial_concurrency: int = POOL_INITIAL_CONCURRENCY,
        *,
        concurrency_plan: Mapping[str, object] | None = None,
    ) -> None:
        event: dict[str, object] = {
            "event": "pool_start",
            "pool_id": pool_id,
            "backend": backend,
            "max_concurrency": max_concurrency,
            "initial_concurrency": initial_concurrency,
        }
        if concurrency_plan is not None:
            event["concurrency_plan"] = dict(concurrency_plan)
        self._write(event)

    def pool_shutdown(self, pool_id: str, completed: int, failed: int, killed: int) -> None:
        self._write(
            {
                "event": "pool_shutdown",
                "pool_id": pool_id,
                "completed": completed,
                "failed": failed,
                "killed": killed,
            }
        )

    def process_start(
        self,
        pid: int | None,
        external_id: str | None,
        backend: str,
        label: str,
    ) -> None:
        self._write(
            {
                "event": "process_start",
                "pid": pid,
                "external_id": external_id,
                "backend": backend,
                "label": label,
            }
        )

    def process_exit(
        self,
        pid: int | None,
        external_id: str | None,
        backend: str,
        label: str,
        exit_code: int | None,
        elapsed_s: float,
        peak_rss_bytes: int | None,
        failure_class: str | None = None,
        error: str | None = None,
    ) -> None:
        data: dict[str, object] = {
            "event": "process_exit",
            "pid": pid,
            "external_id": external_id,
            "backend": backend,
            "label": label,
            "exit_code": exit_code,
            "elapsed_s": round(elapsed_s, 1),
            "peak_rss_bytes": peak_rss_bytes,
        }
        if failure_class is not None:
            data["failure_class"] = failure_class
        if error is not None:
            data["error"] = error
        self._write(data)

    def process_kill(
        self,
        pid: int | None,
        external_id: str | None,
        backend: str,
        label: str,
        kill_reason: str,
        peak_rss_bytes: int | None,
    ) -> None:
        self._write(
            {
                "event": "process_kill",
                "pid": pid,
                "external_id": external_id,
                "backend": backend,
                "label": label,
                "kill_reason": kill_reason,
                "peak_rss_bytes": peak_rss_bytes,
            }
        )

    def host_slot_acquired(
        self,
        *,
        namespace: str,
        slot_id: int,
        limit: int,
        label: str,
        lease_path: str,
    ) -> None:
        """Record host-wide launch admission before a local subprocess starts."""
        self._write(
            {
                "event": "host_slot_acquired",
                "namespace": namespace,
                "slot_id": slot_id,
                "limit": limit,
                "label": label,
                "lease_path": lease_path,
            }
        )

    def host_slot_released(
        self,
        *,
        namespace: str,
        slot_id: int,
        limit: int,
        label: str,
        lease_path: str,
    ) -> None:
        """Record host-wide launch admission release after subprocess exit."""
        self._write(
            {
                "event": "host_slot_released",
                "namespace": namespace,
                "slot_id": slot_id,
                "limit": limit,
                "label": label,
                "lease_path": lease_path,
            }
        )

    def concurrency_adjust(
        self,
        old: int,
        new: int,
        reason: str,
        consecutive_normal: int = 0,
        consecutive_elevated: int = 0,
        *,
        memory_ceiling: int | None = None,
        provider_ceiling: int | None = None,
        operator_cap: int | None = None,
        effective_target: int | None = None,
        bottleneck: str | None = None,
    ) -> None:
        event: dict[str, object] = {
            "event": "concurrency_adjust",
            "old": old,
            "new": new,
            "reason": reason,
            "consecutive_normal": consecutive_normal,
            "consecutive_elevated": consecutive_elevated,
        }
        event.update(
            _without_none(
                {
                    "memory_ceiling": memory_ceiling,
                    "provider_ceiling": provider_ceiling,
                    "operator_cap": operator_cap,
                    "effective_target": effective_target,
                    "bottleneck": bottleneck,
                }
            )
        )
        self._write(event)

    def pressure_check(
        self,
        level: str,
        available_pct: float,
        *,
        swap_used_gb: float | None = None,
        total_memory_gb: float | None = None,
        memory_level: str | None = None,
        swap_delta_gb_per_min: float | None = None,
        swap_level: str | None = None,
        disk_free_gb: float | None = None,
        disk_total_gb: float | None = None,
        disk_used_pct: float | None = None,
        disk_level: str | None = None,
        disk_pressure_cause: str | None = None,
        source: str | None = None,
        current_concurrency: int | None = None,
        active_count: int | None = None,
        pending_count: int | None = None,
        memory_ceiling: int | None = None,
        provider_ceiling: int | None = None,
        operator_cap: int | None = None,
        effective_target: int | None = None,
        bottleneck: str | None = None,
        active_rss_bytes: int | None = None,
        active_peak_rss_bytes: int | None = None,
        active_log_bytes: int | None = None,
    ) -> None:
        event: dict[str, object] = {
            "event": "pressure_check",
            "level": level,
            "available_pct": round(available_pct, 1),
        }
        event.update(
            _without_none(
                {
                    "swap_used_gb": round(swap_used_gb, 2) if swap_used_gb is not None else None,
                    "total_memory_gb": (
                        round(total_memory_gb, 2) if total_memory_gb is not None else None
                    ),
                    "memory_level": memory_level,
                    "swap_delta_gb_per_min": (
                        round(swap_delta_gb_per_min, 2)
                        if swap_delta_gb_per_min is not None
                        else None
                    ),
                    "swap_level": swap_level,
                    "disk_free_gb": round(disk_free_gb, 2) if disk_free_gb is not None else None,
                    "disk_total_gb": (
                        round(disk_total_gb, 2) if disk_total_gb is not None else None
                    ),
                    "disk_used_pct": (
                        round(disk_used_pct, 1) if disk_used_pct is not None else None
                    ),
                    "disk_level": disk_level,
                    "disk_pressure_cause": disk_pressure_cause,
                    "source": source,
                    "current_concurrency": current_concurrency,
                    "active_count": active_count,
                    "pending_count": pending_count,
                    "memory_ceiling": memory_ceiling,
                    "provider_ceiling": provider_ceiling,
                    "operator_cap": operator_cap,
                    "effective_target": effective_target,
                    "bottleneck": bottleneck,
                    "active_rss_bytes": active_rss_bytes,
                    "active_peak_rss_bytes": active_peak_rss_bytes,
                    "active_log_bytes": active_log_bytes,
                }
            )
        )
        self._write(event)

    def health_sample(self, payload: dict[str, object]) -> None:
        """Record a periodic runpool system-health sample.

        ``events.jsonl`` remains the lifecycle stream. ``health.jsonl`` is
        a denser telemetry stream emitted on each pressure tick so operators
        can debug resource issues without reconstructing state from process
        events.
        """
        event: dict[str, object] = {"event": "health_sample"}
        event.update(payload)
        self._write(event)

    def resource_telemetry_error(
        self,
        error: str,
        *,
        action: str,
        current_concurrency: int,
        effective_target: int,
    ) -> None:
        """Record a runpool telemetry failure and the protective action taken."""
        self._write(
            {
                "event": "resource_telemetry_error",
                "error": error,
                "action": action,
                "current_concurrency": current_concurrency,
                "effective_target": effective_target,
            }
        )

    def retry_scheduled(self, label: str, attempt: int, backoff_s: float) -> None:
        self._write(
            {
                "event": "retry_scheduled",
                "label": label,
                "attempt": attempt,
                "backoff_s": round(backoff_s, 1),
            }
        )

    def quota_pause_started(self, reset_at: str, buffer_s: float) -> None:
        self._write(
            {
                "event": "quota_pause_started",
                "reset_at": reset_at,
                "buffer_s": buffer_s,
            }
        )

    def quota_pause_tick(self, remaining_s: float) -> None:
        self._write({"event": "quota_pause_tick", "remaining_s": remaining_s})

    def quota_pause_resumed(self) -> None:
        self._write({"event": "quota_pause_resumed"})

    def auth_outcome(self, outcome: dict[str, object]) -> None:
        """Record a per-item pool auth outcome (plan §Phase 5).

        *outcome* is the dict form of an
        :class:`~metaproc.dispatch.pool_dispatch.AuthOutcome` (via
        :func:`dataclasses.asdict`), already containing adapter, label,
        fingerprints, classification/severity/known_bug_signature,
        rotation flag, and retry count. The ``event`` key is added
        here so the JSONL event stream stays uniform with the rest of
        the pool events.

        Operators inspect runpool ``events.jsonl`` streams for
        ``"event":"auth_outcome"`` to aggregate credential health and
        known-bug reoccurrence across a run without reading the pool
        backend.
        """
        event: dict[str, object] = {"event": "auth_outcome"}
        event.update(outcome)
        self._write(event)

    def auth_lease_acquired(self, payload: dict[str, object]) -> None:
        """Record a slot-lease acquisition (schema-v2 companion to ``auth_outcome``).

        Emitted by :class:`~metaproc.dispatch.slot_coordinator.SlotCoordinator`
        BEFORE the subprocess starts, so post-hoc analysis can pair the
        lease with the eventual ``auth_outcome`` at teardown by primary
        key (run_id/step_id/item/attempt/session_log_path) instead of
        timestamp inference. Spec:
        ``docs/arch/arch-metaproc-core.md``.

        *payload* is the dict produced by
        :func:`metaproc.dispatch.pool_dispatch.build_auth_lease_acquired`.
        The ``event`` key is added here so the JSONL stream stays
        uniform with the rest of the pool events.
        """
        event: dict[str, object] = {"event": "auth_lease_acquired"}
        event.update(payload)
        self._write(event)

    def auth_skipped(
        self,
        *,
        step_id: str,
        step_adapter: str,
        configured_adapter: str,
        reason: str = "adapter_mismatch",
    ) -> None:
        """Record that a configured credential pool was not applied to a step."""
        self._write(
            {
                "event": "auth_skipped",
                "schema_version": 1,
                "pool_enabled": False,
                "step_id": step_id,
                "step_adapter": step_adapter,
                "configured_adapter": configured_adapter,
                "reason": reason,
            }
        )

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _without_none(values: dict[str, object | None]) -> dict[str, object]:
    """Return only fields with real values for compact JSONL events."""
    return {key: value for key, value in values.items() if value is not None}
