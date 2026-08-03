"""Named views: ``--health``, ``--cost``, ``--quality``.

Each named view is a thin specialization of the aggregation + filter
primitives that answers a single operational question:

- ``--health`` surfaces failure-mode clusters (silent_failure, error,
  partial), grouped by source + status; the single command that would
  have caught the 2026-05-10 Perplexity outage in one query.
- ``--cost`` reconciles spend across every trace source in one query, split
  into metered (money owed) and subscription (flat plan fee, reported in
  tokens) so the two are never summed.
- ``--quality runbook-completion`` / ``directive-compliance`` are V1
  agent-output validators (implemented in :mod:`metaproc.trace.quality`).
"""

from __future__ import annotations

from typing import Any

from metaproc.adapters.billing import is_subscription
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

SUBSCRIPTION_EQUIV_COLUMN = "list_equiv_usd"
"""Column name for a subscription row's dollar figure.

Deliberately not ``cost``: under a flat plan fee this is what the tokens would
have cost metered, not money owed. The name travels into ``--cost --json``, so
downstream consumers cannot mistake it for spend either."""


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


def cost_rows(spans: list[TraceEvent]) -> dict[str, list[dict[str, Any]]]:
    """Split spend into its two billing classes; never merge them.

    Returns ``{"metered": rows, "subscription": rows}``.

    - **metered** rows carry ``cost``: money owed, from ``cost.usd`` or a
      metered attempt's ``attempt.cost_usd``. Summing these is meaningful.
    - **subscription** rows carry ``tokens_in`` / ``tokens_out`` plus a
      ``list_equiv`` column. The tokens are the real quantity consumed against
      a flat plan fee; ``list_equiv`` is only what those tokens would have cost
      metered. Summing ``list_equiv`` into a spend figure would be wrong, so
      the two classes are returned separately and rendered separately.

    A subscription attempt is identified by ``attempt.billing_class``; its
    dollar figure lives in ``attempt.cost_list_equiv_usd``, never in
    ``cost.usd``, so no caller can pick it up by accident.
    """
    metered: list[TraceEvent] = []
    subscription: list[TraceEvent] = []

    for s in spans:
        attrs = s.attributes
        if is_subscription(attrs.get("attempt.billing_class")):
            equiv = attrs.get("attempt.cost_list_equiv_usd")
            subscription.append(
                _with_cost_usd(s, float(equiv)) if isinstance(equiv, (int, float)) else s
            )
            continue
        if attrs.get("cost.usd") is not None:
            metered.append(s)
            continue
        # Metered attempt spans keep their dollars in attempt.cost_usd (and
        # Claude's raw result field in attempt.total_cost_usd). Promote to
        # cost.usd so the agent total joins cleanly with provider spend.
        for key in ("attempt.cost_usd", "attempt.total_cost_usd"):
            value = attrs.get(key)
            if isinstance(value, (int, float)):
                metered.append(_with_cost_usd(s, float(value)))
                break

    subscription_rows = aggregate(
        subscription,
        group_keys=["source", "adapter.model"],
        metrics=[
            "sum:attempt.tokens_input",
            "sum:attempt.tokens_output",
            "cost",
            "count",
        ],
    )
    # Rename on the way out: a column headed "cost" in a subscription table is
    # the exact ambiguity this split exists to remove.
    for row in subscription_rows:
        row["tokens_in"] = row.pop("sum:attempt.tokens_input")
        row["tokens_out"] = row.pop("sum:attempt.tokens_output")
        row[SUBSCRIPTION_EQUIV_COLUMN] = row.pop("cost")

    return {
        "metered": aggregate(metered, group_keys=["source", "kind"], metrics=["cost", "count"]),
        "subscription": subscription_rows,
    }


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


def format_cost(rows: dict[str, list[dict[str, Any]]]) -> str:
    """Render the two billing classes as two sections with two totals.

    There is deliberately no combined total. Metered dollars are money;
    subscription dollars are a list-price equivalent against a flat plan fee.
    Adding them produces a number that overstates cash by whatever the
    subscription agents did.
    """
    metered = rows.get("metered") or []
    subscription = rows.get("subscription") or []
    if not metered and not subscription:
        return "(no cost data in trace)\n"

    out: list[str] = []
    if metered:
        out.append("Metered (pay-per-call — money owed):")
        out.append(
            format_rollup_table(metered, group_keys=["source", "kind"], metrics=["cost", "count"])
        )
        total = sum(float(r.get("cost") or 0.0) for r in metered)
        out.append(f"total metered spend: ${total:.4f}\n")

    if subscription:
        out.append("Subscription (flat plan fee — tokens are the real quantity):")
        out.append(
            format_rollup_table(
                subscription,
                group_keys=["source", "adapter.model"],
                metrics=["tokens_in", "tokens_out", SUBSCRIPTION_EQUIV_COLUMN, "count"],
            )
        )
        tok_in = sum(float(r.get("tokens_in") or 0.0) for r in subscription)
        tok_out = sum(float(r.get("tokens_out") or 0.0) for r in subscription)
        equiv = sum(float(r.get(SUBSCRIPTION_EQUIV_COLUMN) or 0.0) for r in subscription)
        out.append(
            f"total subscription tokens: {tok_in:,.0f} in / {tok_out:,.0f} out\n"
            f"  (list-price equivalent ${equiv:.4f} — NOT money owed; "
            f"billed as a flat plan fee, do not add to metered spend)\n"
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
