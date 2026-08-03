"""Deterministic reconciliation for the resource-event ledger."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import cast

from pydantic import TypeAdapter

from metaproc.ids import derive_typed_id_from_key
from metaproc.models.resources import (
    CoverageState,
    MeterKey,
    MeterRollup,
    ResourceEvent,
)

_RESOURCE_EVENT_ADAPTER: TypeAdapter[ResourceEvent] = TypeAdapter(ResourceEvent)


def ensure_resource_event_id(event: ResourceEvent) -> ResourceEvent:
    """Return ``event`` with a stable derived identity when it has none."""
    if event.event_id is not None:
        return event
    event_id = derive_typed_id_from_key("evt", _event_identity(event))
    return _RESOURCE_EVENT_ADAPTER.validate_python(
        {**event.model_dump(mode="python"), "event_id": event_id}
    )


def reconcile_resource_events(events: Iterable[ResourceEvent]) -> list[ResourceEvent]:
    """Assign identities, deduplicate identical events, and reject conflicts."""
    accepted: list[ResourceEvent] = []
    seen: dict[str, str] = {}
    for raw_event in events:
        event = ensure_resource_event_id(raw_event)
        event_id = event.event_id
        if event_id is None:
            raise AssertionError("resource event identity generation failed")
        canonical = _canonical_event(event)
        existing = seen.get(event_id)
        if existing is not None:
            if existing != canonical:
                raise ValueError(f"duplicate resource event identity {event_id!r} conflicts")
            continue
        seen[event_id] = canonical
        accepted.append(event)
    return accepted


def _canonical_event(event: ResourceEvent) -> str:
    """Serialize identity-bearing evidence without volatile file metadata."""
    payload = event.model_dump(mode="json", exclude={"event_id"})
    source = cast(dict[str, object], payload["source"])
    source.pop("mtime_ns", None)
    source.pop("size_bytes", None)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _event_identity(event: ResourceEvent) -> str:
    """Prefer producer span identity; fall back to canonical local evidence."""
    if event.span_id is None:
        return _canonical_event(event)
    payload = {
        "event": event.event,
        "span_id": event.span_id,
        "parent_span_id": event.parent_span_id,
        "hierarchy": event.hierarchy.model_dump(mode="json"),
        "source": {
            "kind": event.source.kind,
            "path": event.source.path,
        },
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def aggregate_meter_rollups(events: Sequence[ResourceEvent]) -> list[MeterRollup]:
    """Aggregate exact meter keys from reconciled events."""
    grouped: dict[tuple[str, str, str, str], _MeterAccumulator] = {}
    for event in reconcile_resource_events(events):
        event_id = event.event_id
        if event_id is None:
            raise AssertionError("reconciled event has no identity")
        for quantity in event.meters:
            key = quantity.key.sort_key()
            bucket = grouped.setdefault(key, _MeterAccumulator(key=quantity.key))
            bucket.event_ids.append(event_id)
            bucket.lineage.extend(quantity.lineage)
            if quantity.coverage is CoverageState.MEASURED:
                bucket.actual = _sum_optional(bucket.actual, quantity.actual_quantity)
            elif quantity.coverage is CoverageState.ESTIMATED:
                bucket.estimated = _sum_optional(
                    bucket.estimated,
                    quantity.estimated_quantity,
                )
            else:
                bucket.unmeasured += 1
    return [grouped[key].to_rollup() for key in sorted(grouped)]


class _MeterAccumulator:
    """Mutable internal accumulator for one exact meter key."""

    def __init__(self, *, key: MeterKey) -> None:
        self.key = key
        self.actual: float | None = None
        self.estimated: float | None = None
        self.unmeasured = 0
        self.event_ids: list[str] = []
        self.lineage: list[str] = []

    def to_rollup(self) -> MeterRollup:
        measured_only = self.actual is not None and self.estimated is None and self.unmeasured == 0
        has_complete_estimate = self.estimated is not None and self.unmeasured == 0
        coverage = (
            CoverageState.MEASURED
            if measured_only
            else CoverageState.ESTIMATED
            if has_complete_estimate
            else CoverageState.UNMEASURED
        )
        return MeterRollup(
            key=self.key,
            coverage=coverage,
            actual_quantity=self.actual,
            estimated_quantity=self.estimated,
            unmeasured_event_count=self.unmeasured,
            source_event_ids=sorted(set(self.event_ids)),
            lineage=sorted(set(self.lineage)),
        )


def merge_meter_rollups(groups: Iterable[Sequence[MeterRollup]]) -> list[MeterRollup]:
    """Merge non-overlapping rollup groups while retaining event lineage."""
    by_key: dict[tuple[str, str, str, str], _MeterAccumulator] = {}
    for group in groups:
        for rollup in group:
            key = rollup.key.sort_key()
            bucket = by_key.setdefault(key, _MeterAccumulator(key=rollup.key))
            overlap = set(bucket.event_ids) & set(rollup.source_event_ids)
            if overlap:
                raise ValueError(
                    "overlapping source event identities in meter rollups: "
                    + ", ".join(sorted(overlap))
                )
            bucket.actual = _sum_optional(bucket.actual, rollup.actual_quantity)
            bucket.estimated = _sum_optional(bucket.estimated, rollup.estimated_quantity)
            bucket.unmeasured += rollup.unmeasured_event_count
            bucket.event_ids.extend(rollup.source_event_ids)
            bucket.lineage.extend(rollup.lineage)
    return [by_key[key].to_rollup() for key in sorted(by_key)]


def _sum_optional(left: float | None, right: float | None) -> float | None:
    if right is None:
        return left
    return (left if left is not None else 0.0) + right
