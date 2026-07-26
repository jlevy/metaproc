"""Runpool-log chart extraction for the metaproc plugin.

Lives here (not in metabrowser core) because runpool is a metaproc-domain
concept: the extractor parses runpool ``events.jsonl`` artifacts that
metaproc's runpool process manager writes, and reuses metaproc's
``stats.analysis`` for the one-pass event aggregation.

When Metaproc is not installed, both the kind-detection manifest and the chart
extractor are absent; runpool-log files use MetaBrowser's generic JSONL
fallback.

Memoization uses a bounded LRU keyed on
``(kind, path, mtime_ns, logical_size)``. The cache is per-process
(plugin-scoped) so it doesn't compete with metabrowser core's
agent-log chart cache.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cachetools import LRUCache
from metabrowser import ArtifactPath, register_root_callback

from metaproc.runpool.event_reader import read_runpool_events
from metaproc.stats.analysis import analyze_runpool_events
from metaproc.stats.models import RunPoolAnalysis

_CHARTS_CACHE_MAX = 128

_CHARTS_CACHE: LRUCache[tuple[str, str, int, int], dict[str, Any]] = LRUCache(
    maxsize=_CHARTS_CACHE_MAX
)


def _cache_key(kind: str, artifact: ArtifactPath) -> tuple[str, str, int, int] | None:
    """Stable cache key that invalidates on any byte appended to the file."""
    try:
        st = artifact.disk_path.stat()
        logical_size = artifact.logical_size
    except OSError:
        return None
    return (kind, str(artifact.disk_path), st.st_mtime_ns, logical_size)


def clear_runpool_charts_cache() -> None:
    """Drop every memoized runpool chart payload."""
    _CHARTS_CACHE.clear()


# Registration is module-load lazy via the plugin handler; before first import
# no runpool chart cache exists, so earlier root changes have nothing to clear.
register_root_callback(clear_runpool_charts_cache)


def extract_runpool_charts(filepath: Path) -> dict[str, Any]:
    """Extract chart data from a runpool events JSONL file.

    Transparently handles ``.jsonl.gz`` via :class:`ArtifactPath`.
    Returns ``{summary: {counts, metadata}, charts: [...]}``.
    """
    artifact = ArtifactPath(filepath)
    key = _cache_key("runpool", artifact)
    if key is not None:
        hit = _CHARTS_CACHE.get(key)
        if hit is not None:
            return hit

    events = read_runpool_events(filepath)
    if not events:
        result: dict[str, Any] = {"summary": None, "charts": []}
        if key is not None:
            _CHARTS_CACHE[key] = result
        return result

    analysis = analyze_runpool_events(events)
    charts = _runpool_chart_specs_from_analysis(analysis)

    result = {
        "summary": {
            "counts": analysis.taxonomy_counts,
            "metadata": analysis.metadata,
        },
        "charts": charts,
    }
    if key is not None:
        _CHARTS_CACHE[key] = result
    return result


def _runpool_chart_specs_from_analysis(
    analysis: RunPoolAnalysis,
) -> list[dict[str, Any]]:
    """Build Chart.js-compatible chart specs from analysis results."""
    charts: list[dict[str, Any]] = []

    annotations = [dict(a) for a in analysis.annotations]  # pyright: ignore[reportUnknownArgumentType]

    if analysis.pressure_series:
        pressure_data = [{"x": p.x, "y": p.y} for p in analysis.pressure_series]
        charts.append(
            {
                "id": "memory-pressure",
                "title": "Memory Available",
                "type": "area",
                "x_type": "time",
                "y_label": "%",
                "y_min": 0,
                "y_max": 100,
                "thresholds": [
                    {"value": 50, "color": "var(--chart-series-info)", "label": "Normal"},
                    {"value": 30, "color": "var(--chart-series-warning)", "label": "Elevated"},
                    {"value": 15, "color": "var(--chart-series-error)", "label": "High"},
                ],
                "series": [
                    {
                        "label": "Available",
                        "color": "var(--chart-series-info)",
                        "data": pressure_data,
                    }
                ],
                "annotations": annotations,
            }
        )

    if analysis.running_count_series:
        running_data = [{"x": p.x, "y": p.y} for p in analysis.running_count_series]
        cap_data = [{"x": p.x, "y": p.y} for p in analysis.concurrency_cap_series]
        charts.append(
            {
                "id": "process-count",
                "title": "Running Processes",
                "type": "step",
                "x_type": "time",
                "y_label": "count",
                "y_min": 0,
                "series": [
                    {"label": "Running", "color": "var(--chart-series-info)", "data": running_data},
                    {
                        "label": "Concurrency cap",
                        "color": "var(--chart-series-warning)",
                        "data": cap_data,
                    },
                ],
                "annotations": annotations,
            }
        )

    return charts
