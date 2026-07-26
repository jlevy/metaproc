"""Append-only JSONL writer for `resource-events.jsonl`.

Mirrors the `EventLogger` primitive used by the run-pool and
process-event streams (see ``metaproc/src/metaproc/runpool/events.py``)
but writes typed `ResourceEvent` instances and validates them on the
way out, so only well-formed JSON reaches disk.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Self

from pydantic import TypeAdapter

from metaproc.io import iter_text_lines
from metaproc.models.resources import ResourceEvent

log = logging.getLogger(__name__)

_RESOURCE_EVENT_ADAPTER: TypeAdapter[ResourceEvent] = TypeAdapter(ResourceEvent)


class ResourceEventLogger:
    """Append-only JSONL writer for the typed `ResourceEvent` union.

    Opens lazily (so callers can construct one before knowing whether the
    log will be used). Each ``write()`` call validates the event through
    the discriminated union and flushes to disk so a crashed run still
    yields useful evidence.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
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

    def write(self, event: ResourceEvent) -> None:
        """Validate ``event`` through the discriminated union and append it."""
        if self._file is None:
            log.warning("ResourceEventLogger.write() called before open(); dropping event")
            return
        validated = _RESOURCE_EVENT_ADAPTER.validate_python(event)
        line = _RESOURCE_EVENT_ADAPTER.dump_json(validated).decode()
        self._file.write(line + "\n")
        self._file.flush()


def read_events(path: Path) -> list[ResourceEvent]:
    """Read every event from a `resource-events.jsonl` file as typed models.

    Skips blank lines but raises on malformed JSON or schema violations —
    silent corruption is worse than a loud failure here because the file
    is the audit trail the rest of the resource pipeline reconciles to.
    """
    out: list[ResourceEvent] = []
    # ``iter_text_lines`` reads transparently from ``.jsonl`` or ``.jsonl.gz``.
    for _line_no, line in iter_text_lines(path):
        stripped = line.strip()
        if not stripped:
            continue
        out.append(_RESOURCE_EVENT_ADAPTER.validate_json(stripped))
    return out


def now_iso() -> datetime:
    """Return current UTC time. Centralised so tests can monkeypatch it."""
    return datetime.now(UTC)
