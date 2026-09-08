"""High-level extract + link pipeline.

The CLI is a thin shell over :func:`extract_trace`; tests target this
function directly so the CLI command stays trivial.
"""

from __future__ import annotations

from pathlib import Path

from metaproc.paths import iter_composite_run_dirs
from metaproc.trace.extractors import all_extractors
from metaproc.trace.ids import compute_span_id
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
    extractors = all_extractors()
    for scope_dir in iter_composite_run_dirs(run_dir):
        scope_path = _scope_path(run_dir, scope_dir)
        scope_spans: list[TraceEvent] = []
        for extractor in extractors:
            if scope_dir != run_dir and not getattr(extractor, "scope_local", False):
                continue
            if not extractor.detect(scope_dir):
                continue
            scope_spans.extend(extractor.extract(scope_dir, trace_id=resolved_trace_id))
        _bind_scope(scope_spans, scope_path=scope_path, is_root=scope_dir == run_dir)
        spans.extend(scope_spans)
    return link_and_propagate(spans)


def _scope_path(root: Path, scope: Path) -> str:
    if scope == root:
        return "."
    try:
        return scope.relative_to(root).as_posix()
    except ValueError:
        return scope.name


def _bind_scope(spans: list[TraceEvent], *, scope_path: str, is_root: bool) -> None:
    """Attach scope identity and namespace nested span relationships in place."""
    id_map = {span.span_id: compute_span_id("scope", scope_path, span.span_id) for span in spans}
    for span in spans:
        span.attributes["scope.path"] = scope_path
        if is_root:
            continue
        original_span_id = span.span_id
        span.span_id = id_map[original_span_id]
        if span.parent_span_id is not None:
            span.parent_span_id = id_map.get(
                span.parent_span_id,
                compute_span_id("scope", scope_path, span.parent_span_id),
            )
