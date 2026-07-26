"""Register metaproc-owned JSONL adapters with MetaBrowser.

MetaBrowser owns the generic adapter registry and normalized event contract. The
metaproc package owns the run-pool and process event schemas, their detectors, and
their parsers. This bridge keeps those domain details out of the standalone browser
package while preserving the existing log and live-stream behavior for consumer workflow.
"""

from __future__ import annotations

from typing import Any

from metabrowser import LogEvent, register_log_adapter

from metaproc.logutil.parsing import (
    LogEvent as MetaprocLogEvent,
)
from metaproc.logutil.parsing import (
    ProcessLogParser,
    RunPoolLogParser,
)

_PROCESS_EVENTS = frozenset(
    {
        "process_start",
        "process_complete",
        "level_start",
        "level_complete",
        "step_start",
        "step_complete",
        "step_fail",
        "step_skip",
        "step_blocked",
        "worker_dispatch",
        "item_start",
        "item_complete",
        "item_fail",
    }
)


def _is_runpool_event(event: dict[str, Any]) -> bool:
    return event.get("event") == "pool_start"


def _is_process_event(event: dict[str, Any]) -> bool:
    event_name = event.get("event")
    return event_name in _PROCESS_EVENTS and (
        "process" in event or "step_id" in event or str(event_name).startswith("item_")
    )


def _to_browser_event(event: MetaprocLogEvent) -> LogEvent:
    return LogEvent(
        kind=event.kind,
        summary=event.summary,
        adapter=event.adapter,
        provider=event.provider,
        timestamp=event.timestamp,
        is_error=event.is_error,
        is_done=event.is_done,
        cost_usd=event.cost_usd,
        duration_s=event.duration_s,
        raw=event.raw,
        summary_parts=event.summary_parts,
    )


class _RunPoolParserBridge:
    adapter_name = "runpool"

    def __init__(self) -> None:
        self._parser = RunPoolLogParser()

    def parse_line(self, line: str) -> list[LogEvent]:
        return [_to_browser_event(event) for event in self._parser.parse_line(line)]

    def flush(self) -> list[LogEvent]:
        return [_to_browser_event(event) for event in self._parser.flush()]


class _ProcessParserBridge:
    adapter_name = "process"

    def __init__(self) -> None:
        self._parser = ProcessLogParser()

    def parse_line(self, line: str) -> list[LogEvent]:
        return [_to_browser_event(event) for event in self._parser.parse_line(line)]

    def flush(self) -> list[LogEvent]:
        return [_to_browser_event(event) for event in self._parser.flush()]


def register_log_adapters() -> None:
    """Install metaproc's adapter detectors and parser factories."""
    register_log_adapter(
        "runpool",
        detector=_is_runpool_event,
        parser_factory=_RunPoolParserBridge,
    )
    register_log_adapter(
        "process",
        detector=_is_process_event,
        parser_factory=_ProcessParserBridge,
    )
