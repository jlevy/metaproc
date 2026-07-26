"""High-level extract + link pipeline.

The CLI is a thin shell over :func:`extract_trace`; tests target this
function directly so the CLI command stays trivial.
"""

from __future__ import annotations

from pathlib import Path

from metaproc.trace.extractors import all_extractors
from metaproc.trace.linker import link_and_propagate
from metaproc.trace.schema import TraceEvent


def extract_trace(run_dir: Path, *, trace_id: str | None = None) -> list[TraceEvent]:
    """Run every detected extractor against ``run_dir``, then run the
    cross-source linker + status propagation pass.

    ``trace_id`` defaults to the run directory's basename — that matches
    the convention every dispatch already uses (the RUN_ID is the dir
    name).
    """
    resolved_trace_id = trace_id or run_dir.name
    spans: list[TraceEvent] = []
    for extractor in all_extractors():
        if not extractor.detect(run_dir):
            continue
        spans.extend(extractor.extract(run_dir, trace_id=resolved_trace_id))
    return link_and_propagate(spans)
