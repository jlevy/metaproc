"""Named views: ``--health``, ``--cost``, ``--quality``.

Each named view is a thin specialization of the aggregation + filter
primitives that answers a single operational question:

- ``--health`` surfaces failure-mode clusters (silent_failure, error,
  partial), grouped by source + status; the single command that would
  have caught the 2026-05-10 Perplexity outage in one query.
- ``--cost`` reconciles cost across every trace source in one query, grouped by
  provenance (provider-reported vs client-side vs pricing-table estimate) so
  amounts obtained in different ways are never summed together.
- ``--quality runbook-completion`` / ``directive-compliance`` are V1
  agent-output validators (implemented in :mod:`metaproc.trace.quality`).
"""

from __future__ import annotations

from typing import Any

from metaproc.adapters.billing import UNKNOWN
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

PROVENANCE_COLUMN = "cost_provenance"
"""Column naming where a row's dollar figure came from.

Travels into ``--cost --json`` alongside the historical ``source``/``kind``/
``cost``/``count`` keys, so consumers can tell a provider-reported amount from a
locally computed estimate without the output shape changing."""


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


_PROVENANCE_ATTR = "attempt.cost_provenance"

# Plugin-supplied cost-kind values mapped onto the provenance vocabulary.
# Producers that emit a `cost.kind` alongside `cost.usd` (the fintool
# ResourceEvent taxonomy does) get classified correctly without having to learn
# a second field.
_COST_KIND_TO_PROVENANCE: dict[str, str] = {
    "actual": "provider_authoritative",
    "provider_returned": "provider_authoritative",
    "estimated": "pricing_table_estimate",
    "computed_from_tokens_and_requests": "pricing_table_estimate",
    "per_request_only": "pricing_table_estimate",
}


def _span_provenance(span: TraceEvent) -> str:
    """Cost provenance for a span, from the most specific signal available.

    Attempt spans carry it directly. Plugin-produced spans carry their dollars
    in top-level ``cost.usd`` and may declare ``cost.provenance`` or a
    ``cost.kind`` from their own taxonomy. A producer that declares neither is
    reported as ``unknown`` rather than being assumed authoritative — an
    unlabeled amount is exactly the case we must not overstate.
    """
    attrs = span.attributes
    for key in (_PROVENANCE_ATTR, "cost.provenance"):
        value = attrs.get(key)
        if isinstance(value, str) and value:
            return value
    kind = attrs.get("cost.kind")
    if isinstance(kind, str):
        mapped = _COST_KIND_TO_PROVENANCE.get(kind)
        if mapped:
            return mapped
    return UNKNOWN


def _with_provenance(span: TraceEvent) -> TraceEvent:
    """Copy of *span* with ``attempt.cost_provenance`` resolved and set."""
    provenance = _span_provenance(span)
    if span.attributes.get(_PROVENANCE_ATTR) == provenance:
        return span
    return span.model_copy(update={"attributes": {**span.attributes, _PROVENANCE_ATTR: provenance}})


def _with_cost_usd(span: TraceEvent, value: float) -> TraceEvent:
    """Copy of *span* carrying ``cost.usd`` so the generic rollup can sum it."""
    return TraceEvent(
        trace_id=span.trace_id,
        span_id=span.span_id,
        parent_span_id=span.parent_span_id,
        name=span.name,
        kind=span.kind,
        source=span.source,
        ts_start=span.ts_start,
        ts_end=span.ts_end,
        duration_ms=span.duration_ms,
        status=span.status,
        attributes={**span.attributes, "cost.usd": float(value)},
    )


def cost_rows(spans: list[TraceEvent]) -> list[dict[str, Any]]:
    """Cost rollup grouped by source, kind, and **cost provenance**.

    Returns the historical top-level array shape; ``cost_provenance`` and the
    token columns are additive. Consumers that read ``source`` / ``kind`` /
    ``cost`` / ``count`` keep working unchanged.

    Grouping by provenance is what keeps the numbers honest. A dollar figure an
    agent CLI computed locally from a bundled price table is not the same kind
    of number as one a provider returned for a call, so the two never collapse
    into a single total — but neither is claimed to be, or not to be, money
    owed, because that depends on plan allowances and purchased credits which
    no run artifact records. See :mod:`metaproc.adapters.billing`.
    """
    spans_with_cost: list[TraceEvent] = []
    for s in spans:
        if s.attributes.get("cost.usd") is not None:
            spans_with_cost.append(_with_provenance(s))
            continue
        # Attempt spans keep their dollars in attempt.cost_usd (and Claude's
        # raw result field in attempt.total_cost_usd). Promote to cost.usd so
        # the agent rows join cleanly with provider rows.
        for key in ("attempt.cost_usd", "attempt.total_cost_usd"):
            value = s.attributes.get(key)
            if isinstance(value, (int, float)):
                spans_with_cost.append(_with_provenance(_with_cost_usd(s, float(value))))
                break

    rows = aggregate(
        spans_with_cost,
        group_keys=["source", "kind", _PROVENANCE_ATTR],
        metrics=["cost", "count", "sum:attempt.tokens_input", "sum:attempt.tokens_output"],
    )
    for row in rows:
        row[PROVENANCE_COLUMN] = row.pop(_PROVENANCE_ATTR)
        row["tokens_in"] = row.pop("sum:attempt.tokens_input")
        row["tokens_out"] = row.pop("sum:attempt.tokens_output")
    return rows


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


_PROVENANCE_SECTIONS: tuple[tuple[str, str], ...] = (
    ("provider_authoritative", "Provider-reported (the provider returned this amount)"),
    ("client_list_estimate", "Client-side estimate (agent CLI's own price table — not a bill)"),
    ("pricing_table_estimate", "Pricing-table estimate (computed here from token counts)"),
    (UNKNOWN, "Unknown provenance (producer declared no cost.provenance or cost.kind)"),
)


def format_cost(rows: list[dict[str, Any]]) -> str:
    """Render one section per cost provenance, each with its own subtotal.

    There is deliberately no single grand total: adding a provider-reported
    amount to a locally estimated one produces a figure that is neither. Each
    section states what kind of number it holds. None of them asserts whether
    the amount is owed — that depends on plan allowances and purchased credits
    that no run artifact records, so it is reported as unknown rather than
    guessed.
    """
    if not rows:
        return "(no cost data in trace)\n"

    metrics = ["cost", "tokens_in", "tokens_out", "count"]
    by_provenance: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_provenance.setdefault(str(row.get(PROVENANCE_COLUMN) or "(none)"), []).append(row)

    ordered = [*_PROVENANCE_SECTIONS]
    known = {key for key, _ in _PROVENANCE_SECTIONS}
    ordered += [
        (key, f"Unclassified provenance ({key})") for key in by_provenance if key not in known
    ]

    out: list[str] = []
    for key, heading in ordered:
        section = by_provenance.get(key)
        if not section:
            continue
        subtotal = sum(float(r.get("cost") or 0.0) for r in section)
        out.append(f"{heading}:")
        out.append(format_rollup_table(section, group_keys=["source", "kind"], metrics=metrics))
        out.append(f"subtotal: ${subtotal:.4f}\n")

    out.append(
        "Amounts are grouped by how they were obtained and are not summed across "
        "groups.\nWhether any of it is owed depends on plan allowances and purchased "
        "credits, which\nare not recorded in run artifacts; charge status is unknown "
        "without billing\nreconciliation.\n"
    )
    return "\n".join(out)


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
