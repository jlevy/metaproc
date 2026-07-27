"""Append-only JSONL event log for the process orchestrator.

Records DAG lifecycle events: process start/complete, step start/
complete/fail/skip/blocked, level transitions, worker dispatch, and
the three per-item lifecycle events (`item_start`, `item_complete`,
`item_fail`).

Every event is validated through the typed `ProcessEvent` discriminated
union (`metaproc.runpool.process_event_models`) on the way out so the
on-disk file always matches the schema downstream consumers (the
resource joiner, `ProcessLogParser`, the browser process-log view) read.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Self

from pydantic import TypeAdapter

from metaproc.runpool.process_event_models import (
    ItemCompleteEvent,
    ItemFailEvent,
    ItemStartEvent,
    LevelCompleteEvent,
    LevelStartEvent,
    ProcessCompleteEvent,
    ProcessEvent,
    ProcessStartEvent,
    StepBlockedEvent,
    StepCompleteEvent,
    StepFailEvent,
    StepSkipEvent,
    StepStartEvent,
    WorkerDispatchEvent,
)

log = logging.getLogger(__name__)


_PROCESS_EVENT_ADAPTER: TypeAdapter[ProcessEvent] = TypeAdapter(ProcessEvent)


class ProcessEventLogger:
    """Typed JSONL writer for orchestrator-level DAG progress.

    Each method constructs a typed `ProcessEvent`, validates it through
    the discriminated union, and appends a single JSON line. Mirrors the
    pattern used by `EventLogger` for runpool events but writes Pydantic
    models instead of plain dicts.
    """

    def __init__(
        self,
        path: Path,
        *,
        subgraph_key: str | None = None,
        process_node_id: str | None = None,
    ) -> None:
        """Open a writer for ``path``.

        ``subgraph_key`` and ``process_node_id`` are stamped onto every
        event written by this logger, so downstream resource joiners do
        not have to reparse log file paths to know which subgraph this
        composite process belongs to. Root-process loggers leave both as
        ``None``.
        """
        self._path = path
        self._subgraph_key = subgraph_key
        self._process_node_id = process_node_id
        self._file: IO[str] | None = None

    @property
    def path(self) -> Path:
        return self._path

    def open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, "a")  # noqa: SIM115

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _write(self, event: ProcessEvent) -> None:
        """Validate ``event`` through the typed union and append it.

        Drops silently with a warning when ``open()`` was never called,
        matching the long-standing `EventLogger._write` behaviour so
        callers can wrap construction in optional code paths.
        """
        if self._file is None:
            log.warning("ProcessEventLogger.write() called before open(); dropping event")
            return
        validated = _PROCESS_EVENT_ADAPTER.validate_python(event)
        line = _PROCESS_EVENT_ADAPTER.dump_json(validated).decode()
        self._file.write(line + "\n")
        self._file.flush()

    # ── Process lifecycle ──────────────────────────────────────────

    def process_start(
        self,
        process: str,
        run_id: str,
        backend: str,
        step_count: int,
    ) -> None:
        self._write(
            ProcessStartEvent(
                ts=self._now(),
                subgraph_key=self._subgraph_key,
                process_node_id=self._process_node_id,
                process=process,
                run_id=run_id,
                backend=backend,
                step_count=step_count,
            )
        )

    def process_complete(
        self,
        process: str,
        run_id: str,
        completed: int,
        failed: int,
        skipped: int,
        elapsed_s: float,
    ) -> None:
        self._write(
            ProcessCompleteEvent(
                ts=self._now(),
                subgraph_key=self._subgraph_key,
                process_node_id=self._process_node_id,
                process=process,
                run_id=run_id,
                completed=completed,
                failed=failed,
                skipped=skipped,
                elapsed_s=round(elapsed_s, 1),
            )
        )

    # ── Level lifecycle ────────────────────────────────────────────

    def level_start(self, level: int, steps: list[str]) -> None:
        self._write(
            LevelStartEvent(
                ts=self._now(),
                subgraph_key=self._subgraph_key,
                process_node_id=self._process_node_id,
                level=level,
                steps=list(steps),
            )
        )

    def level_complete(self, level: int, elapsed_s: float) -> None:
        self._write(
            LevelCompleteEvent(
                ts=self._now(),
                subgraph_key=self._subgraph_key,
                process_node_id=self._process_node_id,
                level=level,
                elapsed_s=round(elapsed_s, 1),
            )
        )

    # ── Step lifecycle ─────────────────────────────────────────────

    def step_start(
        self,
        step_id: str,
        mode: str,
        *,
        step_node_id: str | None = None,
    ) -> None:
        self._write(
            StepStartEvent(
                ts=self._now(),
                subgraph_key=self._subgraph_key,
                process_node_id=self._process_node_id,
                step_id=step_id,
                step_node_id=step_node_id,
                mode=mode,
            )
        )

    def step_complete(
        self,
        step_id: str,
        elapsed_s: float,
        *,
        step_node_id: str | None = None,
    ) -> None:
        self._write(
            StepCompleteEvent(
                ts=self._now(),
                subgraph_key=self._subgraph_key,
                process_node_id=self._process_node_id,
                step_id=step_id,
                step_node_id=step_node_id,
                elapsed_s=round(elapsed_s, 1),
            )
        )

    def step_fail(
        self,
        step_id: str,
        elapsed_s: float,
        error: str = "",
        *,
        step_node_id: str | None = None,
    ) -> None:
        self._write(
            StepFailEvent(
                ts=self._now(),
                subgraph_key=self._subgraph_key,
                process_node_id=self._process_node_id,
                step_id=step_id,
                step_node_id=step_node_id,
                elapsed_s=round(elapsed_s, 1),
                error=error,
            )
        )

    def step_skip(
        self,
        step_id: str,
        reason: str,
        *,
        step_node_id: str | None = None,
    ) -> None:
        self._write(
            StepSkipEvent(
                ts=self._now(),
                subgraph_key=self._subgraph_key,
                process_node_id=self._process_node_id,
                step_id=step_id,
                step_node_id=step_node_id,
                reason=reason,
            )
        )

    def step_blocked(
        self,
        step_id: str,
        *,
        step_node_id: str | None = None,
    ) -> None:
        self._write(
            StepBlockedEvent(
                ts=self._now(),
                subgraph_key=self._subgraph_key,
                process_node_id=self._process_node_id,
                step_id=step_id,
                step_node_id=step_node_id,
            )
        )

    # ── Worker dispatch ────────────────────────────────────────────

    def worker_dispatch(
        self,
        step_id: str,
        num_workers: int,
        items_count: int,
        *,
        step_node_id: str | None = None,
    ) -> None:
        self._write(
            WorkerDispatchEvent(
                ts=self._now(),
                subgraph_key=self._subgraph_key,
                process_node_id=self._process_node_id,
                step_id=step_id,
                step_node_id=step_node_id,
                num_workers=num_workers,
                items_count=items_count,
            )
        )

    # ── Item lifecycle ─────────────────────────────────────────────

    def item_start(
        self,
        step_id: str,
        item_key: str,
        *,
        worker_id: str | None = None,
        step_node_id: str | None = None,
    ) -> None:
        self._write(
            ItemStartEvent(
                ts=self._now(),
                subgraph_key=self._subgraph_key,
                process_node_id=self._process_node_id,
                step_id=step_id,
                step_node_id=step_node_id,
                item_key=item_key,
                worker_id=worker_id,
            )
        )

    def item_complete(
        self,
        step_id: str,
        item_key: str,
        *,
        elapsed_s: float | None = None,
        worker_id: str | None = None,
        step_node_id: str | None = None,
    ) -> None:
        self._write(
            ItemCompleteEvent(
                ts=self._now(),
                subgraph_key=self._subgraph_key,
                process_node_id=self._process_node_id,
                step_id=step_id,
                step_node_id=step_node_id,
                item_key=item_key,
                worker_id=worker_id,
                elapsed_s=round(elapsed_s, 1) if elapsed_s is not None else None,
            )
        )

    def item_fail(
        self,
        step_id: str,
        item_key: str,
        error: str = "",
        *,
        elapsed_s: float | None = None,
        failure_class: str | None = None,
        worker_id: str | None = None,
        step_node_id: str | None = None,
    ) -> None:
        self._write(
            ItemFailEvent(
                ts=self._now(),
                subgraph_key=self._subgraph_key,
                process_node_id=self._process_node_id,
                step_id=step_id,
                step_node_id=step_node_id,
                item_key=item_key,
                worker_id=worker_id,
                elapsed_s=round(elapsed_s, 1) if elapsed_s is not None else None,
                error=error,
                failure_class=failure_class,
            )
        )


def read_process_events(path: Path) -> list[ProcessEvent]:
    """Read every event from a `process-events.jsonl` file as typed models.

    Tolerant of blank lines (editor noise); raises on malformed JSON or
    schema violations because the file is the audit trail downstream
    surfaces depend on.
    """
    out: list[ProcessEvent] = []
    with open(path) as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            out.append(_PROCESS_EVENT_ADAPTER.validate_json(stripped))
    return out


def iter_process_events_raw(path: Path):
    """Yield raw dicts for every event line — used by the legacy parser."""
    with open(path) as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                yield json.loads(stripped)
            except json.JSONDecodeError:
                log.debug("Skipping malformed process-events line: %r", stripped)
                continue
