"""Shared JSONL reader for runpool event files.

Reads runpool ``events.jsonl`` streams and returns typed ``RunPoolEvent`` models.
Used by both ``stats.engine`` and ``browser.charts``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from metaproc.io import ArtifactPath
from metaproc.runpool.event_models import RunPoolEvent

log = logging.getLogger(__name__)

_adapter = TypeAdapter(RunPoolEvent)


def read_runpool_events(filepath: Path) -> list[RunPoolEvent]:
    """Read and parse all events from a runpool JSONL file.

    Transparently handles ``.jsonl.gz`` files via :class:`ArtifactPath`.
    Returns typed event models; malformed lines or unknown event types
    are silently skipped (logged at debug level).
    """
    events: list[RunPoolEvent] = []
    with ArtifactPath(filepath).open_text(errors="replace") as fh:
        for line_no, line in enumerate(fh, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                raw: dict[str, Any] = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                log.debug("Skipping malformed JSON at %s:%d", filepath, line_no)
                continue
            if not isinstance(raw, dict) or "event" not in raw:  # pyright: ignore[reportUnnecessaryIsInstance]
                continue
            try:
                event = _adapter.validate_python(raw)
                events.append(event)
            except ValidationError:
                log.debug(
                    "Skipping unrecognized event at %s:%d: %s",
                    filepath,
                    line_no,
                    raw.get("event"),
                )
    return events
