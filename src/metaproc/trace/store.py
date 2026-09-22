"""Read/write the on-disk trace store at ``<run-dir>/.logs/derived/trace.jsonl``.

The trace is derived from primary logs and state. New runs write it under
``.logs/derived/``; readers fall back to the legacy ``.logs/trace.jsonl``
only for unmarked pre-V2 run directories.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from metaproc import paths as paths_mod
from metaproc.io import atomic_output_file, iter_jsonl_objects
from metaproc.trace.schema import TraceEvent


def trace_path(run_dir: Path) -> Path:
    """Return the V2 trace output path."""
    return paths_mod.trace_out(run_dir)


def write_trace(run_dir: Path, events: Iterable[TraceEvent]) -> Path:
    """Write spans to ``<run-dir>/.logs/derived/trace.jsonl`` and return the path.

    Published atomically: a reader sees the previous trace or the complete new one.
    ``metaproc searches`` and ``metaproc tools`` refuse outright when the trace is
    absent, but a *truncated* trace reads as a valid short one and silently
    under-reports spans — and a plain ``open("w")`` truncates the previous good trace
    before writing the first byte of the new one.

    The spans are streamed into the staged file rather than joined in memory; a trace
    over a long run is large, and staging does not require holding it.
    """
    path = trace_path(run_dir)
    with atomic_output_file(path, make_parents=True) as staged:
        with staged.open("w", encoding="utf-8") as f:
            for ev in events:
                f.write(ev.model_dump_json() + "\n")
    return path


def read_trace(run_dir: Path) -> list[TraceEvent]:
    """Return spans loaded from the trace store. Empty list if absent."""
    path = paths_mod.trace_out_for_read(run_dir)
    if not path.is_file():
        return []
    spans: list[TraceEvent] = []
    for payload in iter_jsonl_objects(path):
        spans.append(TraceEvent.model_validate(payload))
    return spans
