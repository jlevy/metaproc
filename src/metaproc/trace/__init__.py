"""Unified workflow event trace.

See ``docs/project/specs/active/plan-2026-05-11-workflow-event-trace.md``
for the design. Public surface:

- :class:`TraceEvent` — the ``TraceEvent/0.1`` span model.
- :data:`SpanKind`, :data:`SpanStatus` — fixed vocabularies.
- :func:`compute_span_id` — deterministic span ID from source + unique key.
- :func:`link_and_propagate` — cross-source parent reconciliation + status
  propagation pass over an extracted span batch.

Extractors live in :mod:`metaproc.trace.extractors`; the ``metaproc trace``
CLI in :mod:`metaproc.commands.trace`.
"""

from metaproc.trace.ids import compute_span_id
from metaproc.trace.linker import link_and_propagate
from metaproc.trace.schema import (
    SEVERITY_ORDER,
    SPAN_KINDS,
    SPAN_STATUSES,
    SpanKind,
    SpanStatus,
    TraceEvent,
)

__all__ = [
    "SEVERITY_ORDER",
    "SPAN_KINDS",
    "SPAN_STATUSES",
    "SpanKind",
    "SpanStatus",
    "TraceEvent",
    "compute_span_id",
    "link_and_propagate",
]
