"""Group-by aggregation primitive over a trace.

Used by ``metaproc trace --rollup --group <keys> --metric <metrics>``
plus the higher-level ``--cost`` / ``--health`` named views that build
on it. Keep this module a pure function over ``list[TraceEvent]`` so
tests don't need filesystem fixtures.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from metaproc.trace.schema import TraceEvent

_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {"kind", "source", "status", "trace_id", "span_id", "parent_span_id", "name"}
)
"""Group keys read off the TraceEvent fields directly; everything else
is looked up in ``span.attributes``.
"""

_SUPPORTED_METRICS: frozenset[str] = frozenset(
    {
        "count",
        "cost",
        "duration_ms",
        "requests",
        "avg_duration_ms",
    }
)
"""Named metrics with hand-tuned aggregation rules (cost = sum of cost.usd,
requests = sum of provider_call.requests, etc.)."""

_GENERIC_METRIC_PREFIXES: tuple[str, ...] = ("sum:", "avg:", "max:", "min:")
"""Generic prefixed metrics: ``sum:<attr>``, ``avg:<attr>``, ``max:<attr>``,
``min:<attr>``. The suffix is any numeric attribute path
(``attempt.cost_usd``, ``attempt.tokens_input``, etc.). Added by P2.1
(internal issue) so callers can sum/avg the canonical cost+token attrs
written by P1.6 without having to enumerate them here."""


def supported_metrics() -> frozenset[str]:
    return _SUPPORTED_METRICS


def is_supported_metric(metric: str) -> bool:
    """Accept named metrics or any ``<prefix>:<attr>`` generic form."""
    if metric in _SUPPORTED_METRICS:
        return True
    return any(metric.startswith(p) for p in _GENERIC_METRIC_PREFIXES)


def aggregate(
    spans: Iterable[TraceEvent],
    *,
    group_keys: list[str],
    metrics: list[str],
) -> list[dict[str, Any]]:
    """Group ``spans`` by ``group_keys`` and reduce each group to ``metrics``.

    Group keys may be top-level fields (``kind``, ``source``, ``status``,
    ``name``, ``trace_id``, ``span_id``, ``parent_span_id``) or attribute
    paths (``step.id``, ``item.key``, ``tool.name``, etc.).
    Missing values are bucketed under ``"(none)"``.

    Metrics may be named (``count``, ``cost``, ``duration_ms``) or the
    generic ``sum:<attr>`` / ``avg:<attr>`` / ``max:<attr>`` / ``min:<attr>``
    form for any numeric attribute path.

    Returns rows sorted by group key for stable output.
    """
    invalid = [m for m in metrics if not is_supported_metric(m)]
    if invalid:
        msg = (
            f"unsupported metric(s): {sorted(invalid)}. "
            f"Named: {sorted(_SUPPORTED_METRICS)}; "
            f"or use generic forms: sum:<attr>, avg:<attr>, max:<attr>, min:<attr>."
        )
        raise ValueError(msg)
    groups: dict[tuple[str, ...], list[TraceEvent]] = {}
    for span in spans:
        key = tuple(_extract_key(span, gk) for gk in group_keys)
        groups.setdefault(key, []).append(span)

    rows: list[dict[str, Any]] = []
    for key, members in groups.items():
        row: dict[str, Any] = dict(zip(group_keys, key, strict=False))
        for metric in metrics:
            row[metric] = _compute_metric(metric, members)
        rows.append(row)
    rows.sort(key=lambda r: tuple(str(r.get(k, "")) for k in group_keys))
    return rows


def _extract_key(span: TraceEvent, key: str) -> str:
    value = getattr(span, key, None) if key in _TOP_LEVEL_KEYS else span.attributes.get(key)
    if value is None or value == "":
        return "(none)"
    return str(value)


def _compute_metric(metric: str, members: list[TraceEvent]) -> Any:
    if metric == "count":
        return len(members)
    if metric == "cost":
        return round(sum(_attr_numeric(s, "cost.usd") for s in members), 6)
    if metric == "duration_ms":
        return round(sum((s.duration_ms or 0.0) for s in members), 3)
    if metric == "avg_duration_ms":
        durations = [s.duration_ms for s in members if s.duration_ms is not None]
        return round(sum(durations) / len(durations), 3) if durations else None
    if metric == "requests":
        return int(sum(_attr_numeric(s, "provider_call.requests") for s in members))
    # Generic prefixed metrics (P2.1, internal issue).
    for prefix in _GENERIC_METRIC_PREFIXES:
        if metric.startswith(prefix):
            attr = metric[len(prefix) :]
            values = [_attr_numeric(s, attr) for s in members]
            if prefix == "sum:":
                return round(sum(values), 6)
            if prefix == "avg:":
                return round(sum(values) / len(values), 6) if values else None
            if prefix == "max:":
                return max(values) if values else None
            if prefix == "min:":
                return min(values) if values else None
    msg = f"unsupported metric: {metric}"
    raise ValueError(msg)


def _attr_numeric(span: TraceEvent, attr: str) -> float:
    value = span.attributes.get(attr)
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def format_rollup_table(
    rows: list[dict[str, Any]], *, group_keys: list[str], metrics: list[str]
) -> str:
    """Render rollup rows as a fixed-column text table."""
    if not rows:
        return "(no rows)\n"
    columns: list[str] = [*group_keys, *metrics]
    header: list[str] = list(columns)
    body: list[list[str]] = [header]
    for row in rows:
        body.append([_fmt_cell(row.get(c)) for c in columns])
    widths = [max(len(r[i]) for r in body) for i in range(len(columns))]
    lines: list[str] = []
    for row in body:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines) + "\n"


def _fmt_cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        rounded = round(value)
        return f"{int(value)}" if value == float(rounded) else f"{value:.4f}"
    return str(value)
