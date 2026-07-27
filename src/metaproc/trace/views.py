"""View functions for ``metaproc trace``.

C2 ships filter + table + drill + tree views over a loaded trace.
Each function is a small pure operation on ``list[TraceEvent]`` so the
CLI is a thin shell around it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from metaproc.trace.schema import TraceEvent


@dataclass(frozen=True)
class Filter:
    """Filter predicate for span queries.

    ``item_key`` matches ``attributes['item.key']`` — the
    framework-canonical per-item identifier. To filter by a
    domain-specific attribute, pass it via the ``attrs`` mapping.
    """

    kind: str | None = None
    source: str | None = None
    item_key: str | None = None
    step: str | None = None
    tool: str | None = None
    status: str | None = None
    error_code: str | None = None
    attrs: tuple[tuple[str, str], ...] = ()

    def matches(self, span: TraceEvent) -> bool:
        if self.kind and span.kind != self.kind:
            return False
        if self.source and span.source != self.source:
            return False
        if self.item_key and span.attributes.get("item.key") != self.item_key:
            return False
        if self.step and span.attributes.get("step.id") != self.step:
            return False
        if self.tool and span.attributes.get("tool.name") != self.tool:
            return False
        if self.status and span.status != self.status:
            return False
        if self.error_code:
            error = span.error if isinstance(span.error, dict) else None
            if not error or error.get("code") != self.error_code:
                return False
        return all(str(span.attributes.get(key)) == value for key, value in self.attrs)


def apply_filter(spans: Iterable[TraceEvent], flt: Filter) -> list[TraceEvent]:
    return [s for s in spans if flt.matches(s)]


_TABLE_COLUMNS = (
    "ts_start",
    "kind",
    "source",
    "name",
    "step.id",
    "item.key",
    "status",
    "duration_ms",
)


def format_table(spans: list[TraceEvent]) -> str:
    """Render filtered spans as a fixed-column text table. Stable column order."""
    if not spans:
        return "(no spans)\n"
    rows: list[list[str]] = [list(_TABLE_COLUMNS)]
    for s in spans:
        rows.append(
            [
                _short_ts(s.ts_start),
                s.kind,
                s.source,
                _truncate(s.name, 40),
                _truncate(str(s.attributes.get("step.id") or ""), 28),
                str(s.attributes.get("item.key") or ""),
                s.status,
                _fmt_duration(s.duration_ms),
            ]
        )
    widths = [max(len(r[i]) for r in rows) for i in range(len(_TABLE_COLUMNS))]
    lines: list[str] = []
    for row in rows:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines) + "\n"


# ── Projection (P2.1) ──

# Top-level span fields readable as projection columns; everything else
# is looked up in span.attributes.
_TOP_LEVEL_PROJECTION_KEYS: frozenset[str] = frozenset(
    {
        "kind",
        "source",
        "status",
        "name",
        "trace_id",
        "span_id",
        "parent_span_id",
        "ts_start",
        "ts_end",
        "duration_ms",
    }
)


def project_spans(spans: list[TraceEvent], columns: list[str]) -> list[list[str]]:
    """Project each span to the given columns (list of cell strings).

    Each column can be a top-level field (``kind``, ``source``, ``status``,
    ``name``, ``trace_id``, etc.) or an attribute key (``step.id``,
    ``tool.input.query``, etc.). Missing values render as empty string.
    """
    rows: list[list[str]] = []
    for s in spans:
        row: list[str] = []
        for col in columns:
            if col in _TOP_LEVEL_PROJECTION_KEYS:
                value = getattr(s, col, None)
            else:
                value = s.attributes.get(col)
            row.append("" if value is None else str(value))
        rows.append(row)
    return rows


def format_projected_table(rows: list[list[str]], columns: list[str]) -> str:
    """Render projected rows as a fixed-column table with the given header."""
    if not rows:
        return "(no rows)\n"
    body: list[list[str]] = [list(columns), *rows]
    widths = [max(len(r[i]) for r in body) for i in range(len(columns))]
    lines: list[str] = []
    for r in body:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(r)))
    return "\n".join(lines) + "\n"


def format_drill(span: TraceEvent) -> str:
    """Render a full span detail for ``--drill <span-id>``."""
    lines = [
        f"span_id:        {span.span_id}",
        f"trace_id:       {span.trace_id}",
        f"parent_span_id: {span.parent_span_id or '-'}",
        f"name:           {span.name}",
        f"kind:           {span.kind}",
        f"source:         {span.source}",
        f"ts_start:       {span.ts_start}",
        f"ts_end:         {span.ts_end or '-'}",
        f"duration_ms:    {_fmt_duration(span.duration_ms)}",
        f"status:         {span.status}{' (derived)' if span.status_derived else ''}",
    ]
    if span.error:
        lines.append(f"error:          {span.error}")
    if span.attributes:
        lines.append("attributes:")
        for key in sorted(span.attributes):
            lines.append(f"  {key}: {span.attributes[key]}")
    if span.events:
        lines.append("events:")
        for ev in span.events:
            lines.append(f"  - {ev}")
    return "\n".join(lines) + "\n"


def format_tree(spans: list[TraceEvent], root_id: str | None = None) -> str:
    """Render a hierarchical tree of spans. ``root_id`` scopes the view to
    the descendants of one span (plus that span itself).
    """
    by_id = {s.span_id: s for s in spans}
    children: dict[str | None, list[str]] = {}
    for s in spans:
        children.setdefault(s.parent_span_id, []).append(s.span_id)
    for kids in children.values():
        kids.sort(key=lambda sid: by_id[sid].ts_start)

    roots: list[str]
    if root_id is not None:
        if root_id not in by_id:
            return f"(span {root_id} not found)\n"
        roots = [root_id]
    else:
        roots = [s.span_id for s in spans if s.parent_span_id is None]
        roots.sort(key=lambda sid: by_id[sid].ts_start)

    lines: list[str] = []
    for root in roots:
        _walk_tree(root, by_id, children, lines, depth=0)
    return "\n".join(lines) + "\n"


def _walk_tree(
    span_id: str,
    by_id: dict[str, TraceEvent],
    children: dict[str | None, list[str]],
    lines: list[str],
    *,
    depth: int,
) -> None:
    span = by_id.get(span_id)
    if span is None:
        return
    indent = "  " * depth
    suffix = f" [{span.status}]" if span.status != "ok" else ""
    duration = f" ({_fmt_duration(span.duration_ms)})" if span.duration_ms else ""
    lines.append(f"{indent}{span.kind}: {span.name}{suffix}{duration}")
    for child in children.get(span_id, ()):
        _walk_tree(child, by_id, children, lines, depth=depth + 1)


def _short_ts(ts: str) -> str:
    if not ts:
        return ""
    # ISO-8601 → just keep date + HH:MM:SS for the table
    return ts[:19].replace("T", " ")


def _truncate(value: str, n: int) -> str:
    return value if len(value) <= n else value[: n - 1] + "…"


def _fmt_duration(ms: float | None) -> str:
    if ms is None:
        return "-"
    if ms >= 60_000:
        return f"{ms / 60_000:.1f}m"
    if ms >= 1_000:
        return f"{ms / 1_000:.1f}s"
    return f"{ms:.0f}ms"
