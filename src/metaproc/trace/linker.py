"""Cross-source parent reconciliation + status propagation.

Each extractor emits spans for its own source with ``parent_span_id`` set
only when the parent lives in the same source (e.g. a ``tool_call``'s
parent ``agent_session`` is in the same per-attempt JSONL). The linker
runs after all extractors have produced their spans and:

1. Sets ``parent_span_id`` across sources using attribute joins:
   ``attempt`` → ``item`` (by ``step.id`` + ``item.key``); ``subprocess``
   → ``step`` (by ``step.id``); ``provider_call`` → ``subprocess``
   (by ``subprocess.out_path``).
2. Propagates status upward by severity: a parent's ``status`` is at
   least as severe as the worst child. Original per-source status is
   preserved in ``attributes['source.status']``.

Linking is deterministic, idempotent, and operates in O(n) over the span
list (a single attribute-keyed index per join). All attribute names used
here are generic — workflows that add domain-specific attributes can
coexist; the linker only requires the framework-canonical ``item.key``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import cast

from metaproc.trace.schema import SEVERITY_ORDER, SpanStatus, TraceEvent


def link_and_propagate(events: Iterable[TraceEvent]) -> list[TraceEvent]:
    """Return a new list of spans with cross-source parents + propagated status.

    Idempotent: running twice produces the same output.
    """
    spans: list[TraceEvent] = list(events)
    _link_cross_source_parents(spans)
    _propagate_status_upward(spans)
    return spans


def _link_cross_source_parents(spans: list[TraceEvent]) -> None:
    """Set ``parent_span_id`` on spans that point cross-source.

    Mutates in place. Skips spans that already have a parent set within
    their own source.
    """
    by_id: dict[str, TraceEvent] = {s.span_id: s for s in spans}
    items_by_key = _index_items(spans)
    steps_by_id = _index_steps(spans)
    subprocesses_by_out_path = _index_subprocesses_by_out_path(spans)

    for s in spans:
        if s.parent_span_id and s.parent_span_id in by_id:
            continue
        parent_id = _resolve_cross_source_parent(
            s,
            items_by_key=items_by_key,
            steps_by_id=steps_by_id,
            subprocesses_by_out_path=subprocesses_by_out_path,
        )
        if parent_id is not None:
            s.parent_span_id = parent_id


def _resolve_cross_source_parent(
    span: TraceEvent,
    *,
    items_by_key: dict[tuple[str, str, str], str],
    steps_by_id: dict[tuple[str, str], str],
    subprocesses_by_out_path: dict[tuple[str, str], str],
) -> str | None:
    """Return a candidate ``parent_span_id`` from cross-source indexes, or ``None``."""
    attrs = span.attributes
    scope_path = _scope_path(attrs)
    if span.kind == "attempt":
        step_id = attrs.get("step.id")
        item_key = attrs.get("item.key")
        if step_id and item_key:
            item_parent = items_by_key.get((scope_path, str(step_id), str(item_key)))
            if item_parent is not None:
                return item_parent
        if step_id:
            return steps_by_id.get((scope_path, str(step_id)))
    elif span.kind == "subprocess":
        step_id = attrs.get("step.id")
        if step_id:
            return steps_by_id.get((scope_path, str(step_id)))
    elif span.kind == "provider_call":
        out_path = attrs.get("subprocess.out_path")
        if out_path:
            return subprocesses_by_out_path.get((scope_path, str(out_path)))
    return None


def _scope_path(attrs: dict[str, object]) -> str:
    value = attrs.get("scope.path")
    return str(value) if value else "."


def _index_items(spans: list[TraceEvent]) -> dict[tuple[str, str, str], str]:
    """``(scope.path, step.id, item.key) → item span_id``.

    ``item.key`` is the framework-canonical per-item identifier (set by
    the metaproc engine from ``for_each.key``). Workflows may also add
    domain-specific attributes; the linker only joins on the generic
    key.
    """
    out: dict[tuple[str, str, str], str] = {}
    for s in spans:
        if s.kind != "item":
            continue
        step_id = s.attributes.get("step.id")
        item_key = s.attributes.get("item.key")
        if step_id and item_key:
            out[(_scope_path(s.attributes), str(step_id), str(item_key))] = s.span_id
    return out


def _index_steps(spans: list[TraceEvent]) -> dict[tuple[str, str], str]:
    """``(scope.path, step.id) → step span_id``."""
    out: dict[tuple[str, str], str] = {}
    for s in spans:
        if s.kind != "step":
            continue
        step_id = s.attributes.get("step.id")
        if step_id:
            out[(_scope_path(s.attributes), str(step_id))] = s.span_id
    return out


def _index_subprocesses_by_out_path(spans: list[TraceEvent]) -> dict[tuple[str, str], str]:
    """``(scope.path, subprocess.out_path) → subprocess span_id``.

    Used by ``provider_call`` joins: when a subprocess writes its
    output to a specific path and a separate extractor later reads
    that path as the parent artifact for sub-call spans, the output
    path is the join key. Generic to any subprocess kind — arena
    tools, build commands, fetchers all use the same convention.
    """
    out: dict[tuple[str, str], str] = {}
    for s in spans:
        if s.kind != "subprocess":
            continue
        out_path = s.attributes.get("subprocess.out_path")
        if out_path:
            out[(_scope_path(s.attributes), str(out_path))] = s.span_id
    return out


def _propagate_status_upward(spans: list[TraceEvent]) -> None:
    """Propagate ``status`` from children to parents by severity.

    For every span, set its ``status`` to the highest-severity status
    seen across itself + all descendants. Preserves the original
    per-source status under ``attributes['source.status']``.
    """
    by_id: dict[str, TraceEvent] = {s.span_id: s for s in spans}
    children: dict[str, list[str]] = {}
    for s in spans:
        if s.parent_span_id:
            children.setdefault(s.parent_span_id, []).append(s.span_id)

    for s in spans:
        if "source.status" not in s.attributes:
            s.attributes["source.status"] = s.status

    memo: dict[str, str] = {}

    def worst(span_id: str) -> str:
        if span_id in memo:
            return memo[span_id]
        span = by_id.get(span_id)
        if span is None:
            return "unknown"
        max_status = span.status
        max_sev = SEVERITY_ORDER[max_status]
        for child_id in children.get(span_id, ()):
            child_span = by_id.get(child_id)
            if child_span is not None and child_span.attributes.get("error.recovered") is True:
                continue
            child_worst = worst(child_id)
            child_sev = SEVERITY_ORDER[child_worst]
            if child_sev > max_sev:
                max_status = child_worst
                max_sev = child_sev
        memo[span_id] = max_status
        return max_status

    for s in spans:
        propagated = worst(s.span_id)
        if propagated != s.status:
            s.status = cast(SpanStatus, propagated)
