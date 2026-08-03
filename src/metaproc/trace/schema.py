"""``TraceEvent/0.1`` span schema.

The trace is consumed programmatically (by the linker, the
``metaproc trace`` CLI, and a future OTel exporter), so Pydantic earns
its keep here.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SpanKind = Literal[
    "run",
    "level",
    "step",
    "item",
    "attempt",
    "agent_session",
    "tool_call",
    "subprocess",
    "llm_call",
    "provider_call",
    "internal",
]
"""See plan § Span kinds.

Flat vocabulary. Hierarchy is expressed via ``parent_span_id``. The
framework deliberately keeps this list minimal and generic:
workflow-specific subprocess invocations (arena tools for consumer workflow,
build tools for code review, etc.) all map to ``subprocess`` with a
``subprocess.tool`` attribute that names the specific tool.
"""

SpanStatus = Literal["ok", "error", "partial", "silent_failure", "unknown"]
"""Span status. ``silent_failure`` is the synthesized status for cases like
Perplexity ``requests == 0`` while ``success == true`` (the 2026-05-10 audit
finding).
"""

SPAN_KINDS: frozenset[str] = frozenset(
    {
        "run",
        "level",
        "step",
        "item",
        "attempt",
        "agent_session",
        "tool_call",
        "subprocess",
        "llm_call",
        "provider_call",
        "internal",
    }
)

SPAN_STATUSES: frozenset[str] = frozenset({"ok", "error", "partial", "silent_failure", "unknown"})

SEVERITY_ORDER: dict[str, int] = {
    "ok": 0,
    "unknown": 0,
    "partial": 1,
    "silent_failure": 2,
    "error": 3,
}
"""Severity ranking for status propagation. ``unknown`` is treated as ``ok``
for propagation (don't escalate up from missing data); explicit ``ok`` is
the same rank."""

TOOL_USAGE_ATTRS: frozenset[str] = frozenset(
    {
        "tool.name",
        "tool.family",
        "tool.operation",
        "tool.usage_role",
        "tool.input.query",
        "tool.input.url",
        "tool.input.ref",
        "tool.input.path",
        "tool.input.command",
        "tool.input.digest",
        "tool.input.prompt_summary",
        "tool.invoked.name",
        "tool.edit_count",
        "tool.result.size_bytes",
        "tool.result.truncated",
        "tool.result.path",
        "tool.result.digest",
        "tool.result.ref",
        "provider",
        "source_origin",
        "registry_source_id",
        "cutoff_status",
        "raw_log_ref",
        "artifact_namespace",
        "execution_profile",
        "lane_id",
    }
)
"""TraceEvent/0.1 adapter-neutral tool-usage attribute vocabulary.

These keys are intentionally stored in ``TraceEvent.attributes`` rather than
as model fields so older events remain valid and workflow-owned extensions can
coexist with the common trace contract.
"""

COST_UNAVAILABLE = "unavailable"
"""Cost/token telemetry sentinel.

``None`` or an absent attribute means a span does not have cost telemetry.
``COST_UNAVAILABLE`` means cost telemetry was expected for that adapter/source
but the raw tool output did not provide it.
"""


def tool_usage_attrs(**values: Any) -> dict[str, Any]:
    """Return normalized tool-usage attributes, omitting null/empty values.

    Keyword names use Python identifiers with ``__`` in place of dots:
    ``tool__family="web"`` becomes ``{"tool.family": "web"}``.
    The helper is deliberately permissive; it only normalizes and filters
    values, while ``TOOL_USAGE_ATTRS`` documents the code-owned stable keys.
    """
    attrs: dict[str, Any] = {}
    for key, value in values.items():
        if value is None or value == "":
            continue
        attrs[key.replace("__", ".")] = value
    return attrs


def bounded_attr_text(value: object, *, max_chars: int = 500) -> str | None:
    """Return a bounded string for safe trace attributes."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped[:max_chars]


class TraceEvent(BaseModel):
    """One span in the unified trace. JSONL-serializable.

    Plan reference: § Design / TraceEvent/0.1 schema.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    trace_id: str
    """The dispatch's RUN_ID. Already unique per workflow invocation in our system."""

    span_id: str
    """SHA-1 of ``(source, source_specific_unique_key)`` truncated to 16 chars
    via :func:`metaproc.trace.ids.compute_span_id`. Deterministic so
    re-extracting the same logs produces stable IDs."""

    parent_span_id: str | None = None
    """Null at the root span. Cross-source parents are resolved by the linker."""

    name: str
    """Human-readable: tool name, step ID, event name, etc."""

    kind: SpanKind
    """See :data:`SPAN_KINDS`."""

    source: str
    """Which extractor produced this span. Stable strings like
    ``claude-agent``, ``external-wrapper``, ``web-bundle``, ``metaproc-engine``."""

    ts_start: str
    """ISO-8601 UTC timestamp. Required."""

    ts_end: str | None = None
    """ISO-8601 UTC timestamp. ``None`` for instantaneous events (no duration)."""

    duration_ms: float | None = None
    """Span duration in milliseconds. ``None`` when ``ts_end`` is absent."""

    status: SpanStatus = "unknown"
    """See :data:`SPAN_STATUSES`. Defaults to ``unknown`` until either an
    extractor sets it from a raw signal or the linker propagates from children."""

    status_derived: bool = False
    """``True`` if the extractor synthesized the status from raw signals
    (e.g. ``silent_failure`` from ``requests == 0``) rather than reading
    an explicit error indicator from the source log."""

    error: dict[str, Any] | None = None
    """Optional error payload when ``status != ok``. Shape is extractor-specific
    but typically ``{kind: str, message: str, ...}``."""

    attributes: dict[str, Any] = Field(default_factory=dict)
    """Free-form key-value attributes. Convention: dotted namespaces like
    ``run.id``, ``step.id``, ``item.key``, ``tool.name``, ``cost.usd``.
    Selectively include payloads — large inputs/outputs become digests or
    paths, not inline bytes.

    A producer emitting ``cost.usd`` should also declare where the figure came
    from, via ``cost.provenance`` (``provider_authoritative`` /
    ``client_list_estimate`` / ``pricing_table_estimate``) or its own
    ``cost.kind``. Amounts with neither are reported as unknown provenance
    rather than assumed authoritative — see :mod:`metaproc.adapters.billing`."""

    events: list[dict[str, Any]] = Field(default_factory=list)
    """Sparse list of mid-span events: rate-limits, retries, internal markers."""

    def severity(self) -> int:
        """Numeric severity for propagation. Higher = more severe."""
        return SEVERITY_ORDER[self.status]
