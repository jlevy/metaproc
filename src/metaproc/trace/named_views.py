"""Named views: ``--health``, ``--cost``, ``--quality``.

Each named view is a thin specialization of the aggregation + filter
primitives that answers a single operational question:

- ``--health`` surfaces failure-mode clusters (silent_failure, error,
  partial), grouped by source + status; the single command that would
  have caught the 2026-05-10 Perplexity outage in one query.
- ``--cost`` reconciles cost across every trace source in one query.
- ``--quality runbook-completion`` / ``directive-compliance`` are V1
  agent-output validators (implemented in :mod:`metaproc.trace.quality`).
"""

from __future__ import annotations

from typing import Any

from metaproc.trace.aggregation import aggregate, format_rollup_table
from metaproc.trace.schema import TraceEvent
from metaproc.trace.views import Filter, apply_filter

_NON_OK_STATUSES: frozenset[str] = frozenset({"error", "partial", "silent_failure", "unknown"})

_HEALTH_MESSAGE_PREFIX_LEN = 60
"""Trim ``error.message`` to this many chars when used as a grouping key,
so two quota-exhausted spans with identical leading text cluster even if
the trailing detail (timestamps, item keys) differs."""

SOURCE_TOOL_GROUP_KEYS: tuple[str, ...] = (
    "item.key",
    "step.id",
    "source",
    "execution_profile",
    "tool.family",
    "tool.operation",
    "source_origin",
    "provider",
    "registry_source_id",
    "status",
    "cutoff_status",
)
"""Default generic source/tool rollup dimensions."""

_SOURCE_TOOL_KINDS: frozenset[str] = frozenset(
    {"tool_call", "subprocess", "provider_call", "llm_call"}
)


def health_rows(spans: list[TraceEvent]) -> list[dict[str, Any]]:
    """Return health-rollup rows for non-ok statuses, sorted by source then
    severity (silent_failure / error first).

    When a span carries ``error.code`` (e.g. ``quota_exhausted`` from the
    claude-agent extractor), rows include ``error.code`` and a
    ``error.message`` prefix so the 2026-05-13 quota cascade clusters as
    a single line ("claude-agent / error / quota_exhausted / 191") rather
    than 191 unattributed errors.

    Legacy spans without ``error.code`` still group by ``(source, status)``
    only — the new columns are empty for them.
    """
    severe = [s for s in spans if s.status in _NON_OK_STATUSES and s.status != "unknown"]

    groups: dict[tuple[str, str, str, str], int] = {}
    for span in severe:
        code, message_prefix = _error_key(span)
        key = (span.source, span.status, code, message_prefix)
        groups[key] = groups.get(key, 0) + 1

    rows: list[dict[str, Any]] = [
        {
            "source": source,
            "status": status,
            "error.code": code,
            "error.message": message_prefix,
            "count": count,
        }
        for (source, status, code, message_prefix), count in groups.items()
    ]
    rows.sort(
        key=lambda r: (
            str(r["source"]),
            -_severity_rank(str(r["status"])),
            str(r.get("error.code", "")),
        )
    )
    return rows


def _error_key(span: TraceEvent) -> tuple[str, str]:
    """Extract ``(code, message_prefix)`` group keys from a span's ``error``
    dict. Returns ``("", "")`` when the span has no classified error info.
    """
    if not isinstance(span.error, dict):
        return ("", "")
    code = span.error.get("code") or ""
    message = span.error.get("message") or ""
    if not isinstance(code, str):
        code = ""
    if not isinstance(message, str):
        message = ""
    # rstrip so the rollup table never carries trailing whitespace when the
    # slice lands mid-word.
    return (code, message[:_HEALTH_MESSAGE_PREFIX_LEN].rstrip())


def cost_rows(spans: list[TraceEvent]) -> list[dict[str, Any]]:
    """Return ``[{source, kind, cost, count}]`` rows for all spans that
    carry a ``cost.usd`` attribute OR have a ``total_cost_usd`` claude
    attribute on attempt spans.

    Splits cost by source so independent provider and agent totals are visible
    side-by-side.
    """
    spans_with_cost: list[TraceEvent] = []
    for s in spans:
        if s.attributes.get("cost.usd") is not None:
            spans_with_cost.append(s)
            continue
        # Claude attempt spans carry attempt.total_cost_usd from the
        # session's `result` event. Promote that to cost.usd for the
        # rollup so the agent total joins cleanly with the rest.
        total_cost = s.attributes.get("attempt.total_cost_usd")
        if isinstance(total_cost, (int, float)):
            promoted = TraceEvent(
                trace_id=s.trace_id,
                span_id=s.span_id,
                parent_span_id=s.parent_span_id,
                name=s.name,
                kind=s.kind,
                source=s.source,
                ts_start=s.ts_start,
                ts_end=s.ts_end,
                duration_ms=s.duration_ms,
                status=s.status,
                attributes={**s.attributes, "cost.usd": float(total_cost)},
            )
            spans_with_cost.append(promoted)

    return aggregate(
        spans_with_cost,
        group_keys=["source", "kind"],
        metrics=["cost", "count"],
    )


def source_tool_rows(spans: list[TraceEvent]) -> list[dict[str, Any]]:
    """Return a generic source/tool rollup over trace tool-usage attrs.

    The view is intentionally workflow-neutral: all dimensions are either
    top-level trace fields or TraceEvent/0.1 tool-usage attributes, so large workflow
    and future workflows can filter/render without a separate provenance log.
    """
    groups: dict[tuple[str, ...], list[TraceEvent]] = {}
    for span in spans:
        if not _is_source_tool_span(span):
            continue
        key = tuple(_source_tool_value(span, group_key) for group_key in SOURCE_TOOL_GROUP_KEYS)
        groups.setdefault(key, []).append(span)

    rows: list[dict[str, Any]] = []
    for key, members in groups.items():
        raw_log_ref_count = sum(1 for member in members if member.attributes.get("raw_log_ref"))
        row: dict[str, Any] = dict(zip(SOURCE_TOOL_GROUP_KEYS, key, strict=False))
        row["count"] = len(members)
        row["raw_log_ref.count"] = raw_log_ref_count
        row["raw_log_ref.coverage_pct"] = round(raw_log_ref_count * 100 / len(members), 1)
        rows.append(row)
    rows.sort(key=lambda r: tuple(str(r.get(k, "")) for k in SOURCE_TOOL_GROUP_KEYS))
    return rows


def format_source_tool(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "(no source/tool spans in trace)\n"
    return format_rollup_table(
        rows,
        group_keys=list(SOURCE_TOOL_GROUP_KEYS),
        metrics=["count", "raw_log_ref.count", "raw_log_ref.coverage_pct"],
    )


def format_health(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "(no failures, partials, or silent_failures in trace)\n"
    # Show the error.code / error.message columns only when at least one row
    # carries them, so older traces (no claude-agent classification) keep
    # the original compact two-column rollup.
    has_codes = any(row.get("error.code") for row in rows)
    group_keys = ["source", "status"]
    if has_codes:
        group_keys = [*group_keys, "error.code", "error.message"]
    return format_rollup_table(rows, group_keys=group_keys, metrics=["count"])


def format_cost(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "(no cost data in trace)\n"
    total = sum(float(r.get("cost") or 0.0) for r in rows)
    body = format_rollup_table(rows, group_keys=["source", "kind"], metrics=["cost", "count"])
    return body + f"\ntotal cost across trace: ${total:.4f}\n"


def _severity_rank(status: str) -> int:
    order = {"silent_failure": 3, "error": 2, "partial": 1, "unknown": 0, "ok": 0}
    return order.get(status, 0)


def _is_source_tool_span(span: TraceEvent) -> bool:
    if span.kind in _SOURCE_TOOL_KINDS:
        return True
    return any(
        key in span.attributes
        for key in ("tool.family", "tool.operation", "source_origin", "provider")
    )


def _source_tool_value(span: TraceEvent, key: str) -> str:
    if key == "source":
        value = span.source
    elif key == "status":
        value = span.status
    else:
        value = span.attributes.get(key)
    if value is None or value == "":
        return "(none)"
    return str(value)


__all__ = [
    "SOURCE_TOOL_GROUP_KEYS",
    "cost_rows",
    "format_cost",
    "format_health",
    "format_source_tool",
    "health_rows",
    "source_tool_rows",
]


# Silence unused-import warnings for the apply_filter / Filter symbols we
# re-export so other named views can use them in the same module.
_ = (apply_filter, Filter)
