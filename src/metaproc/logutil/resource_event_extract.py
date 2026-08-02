"""Convert parsed LogEvents into typed `ResourceEvent` instances.

The roll-up builder needs a per-line evidence trail, not just file-level
usage totals. This module walks a parsed `LogFile` and its `LogEvent`
stream, then emits one typed `ResourceEvent` per attributable atom such as
tool calls, throttling windows, session usage, process item lifecycle,
local samples, and billing estimates.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from metaproc.cloud.gcp.billing import billable_for_span
from metaproc.logutil.parsing import LogEvent, LogFile
from metaproc.logutil.throttling import ThrottleSpan, attribute_throttling
from metaproc.logutil.tool_spans import ToolSpan, pair_tool_events
from metaproc.logutil.usage import compute_cost, load_pricing
from metaproc.models.node_ids import ROOT_SUBGRAPH_KEY, process_node_id, step_node_id
from metaproc.models.resources import (
    BillingEvent,
    CoverageState,
    HierarchyRef,
    ItemCompleteEvent,
    ItemFailEvent,
    ItemStartEvent,
    MeteredQuantity,
    MeterKey,
    Metrics,
    ProviderMeterObservation,
    ProviderRef,
    ResourceEvent,
    SampleEvent,
    SourceKind,
    SourceRef,
    TaxonomyPaths,
    ToolCallEvent,
    UsageEvent,
    WaitEvent,
)
from metaproc.plugins.protocol import ProviderMeterSource


def extract_resource_events(
    *,
    log_path: Path,
    log_file: LogFile,
    log_events: list[LogEvent],
    hierarchy: HierarchyRef,
    source_kind: SourceKind,
    source_path: str,
    source_size_bytes: int | None = None,
    source_mtime_ns: int | None = None,
    provider_meter_sources: Sequence[ProviderMeterSource] = (),
) -> list[ResourceEvent]:
    """Return one typed `ResourceEvent` per attributable atom in ``log_events``.

    Composes the existing pairing helpers so this module stays small and
    the classification rules live in one place.
    """
    out: list[ResourceEvent] = []
    base_source = SourceRef(
        kind=source_kind,
        path=source_path,
        size_bytes=source_size_bytes,
        mtime_ns=source_mtime_ns,
    )

    # Tool calls.
    for tool_span in pair_tool_events(log_events):
        out.append(_tool_call_event(tool_span, hierarchy, base_source))

    # Throttling windows.
    for throttle_span in attribute_throttling(log_events):
        if throttle_span.duration_s is None or throttle_span.duration_s <= 0:
            # Allowed-warning markers are useful for evidence count but don't
            # contribute time; skip emitting wait events with zero duration to
            # keep the events file lean.
            continue
        out.append(_wait_event(throttle_span, hierarchy, base_source))

    provider_observations: list[ProviderMeterObservation] = []
    for provider_source in provider_meter_sources:
        provider_observations.extend(
            provider_source.extract(
                log_path=log_path,
                log_file=log_file,
                log_events=log_events,
                hierarchy=hierarchy,
                source_path=source_path,
            )
        )

    # Session-level usage (one event per file when LogFile picked up usage_stats).
    # Provider-specific observations below remain separate child evidence so nested
    # requests never inflate the agent session's token or cost totals.
    authoritative_keys = {
        meter.key.sort_key()
        for observation in provider_observations
        for meter in observation.meters
    }
    usage_event = _usage_event_for_file(
        log_file,
        hierarchy,
        base_source,
        authoritative_meter_keys=authoritative_keys,
    )
    if usage_event is not None:
        out.append(usage_event)
    out.extend(
        _provider_observation_event(observation, hierarchy, base_source)
        for observation in provider_observations
    )

    # Runpool process_exit → Sample (always) + Billing (when machine_type known)
    if source_kind == "runpool_events":
        out.extend(_runpool_events_from_log(log_events, hierarchy, base_source))

    if source_kind == "process_events":
        out.extend(_item_lifecycle_events_from_log(log_events, hierarchy, base_source))

    return out


def _item_lifecycle_events_from_log(
    log_events: list[LogEvent],
    hierarchy: HierarchyRef,
    source: SourceRef,
) -> list[ResourceEvent]:
    out: list[ResourceEvent] = []
    for ev in log_events:
        if ev.adapter != "process" or not isinstance(ev.raw, dict):
            continue
        raw: dict[str, object] = ev.raw
        event_type = raw.get("event")
        if event_type not in ("item_start", "item_complete", "item_fail"):
            continue

        item_key = raw.get("item_key")
        step_id = raw.get("step_id")
        if not isinstance(item_key, str) or not isinstance(step_id, str):
            continue

        item_hierarchy = _hierarchy_from_process_event(raw, hierarchy)
        elapsed_s = _float_or_none(raw.get("elapsed_s"))
        metrics = Metrics(wall_time_s=elapsed_s) if elapsed_s is not None else Metrics()
        ts_value = _ts_from_event(ev, source)

        if event_type == "item_start":
            out.append(
                ItemStartEvent(
                    ts=ts_value,
                    hierarchy=item_hierarchy,
                    metrics=metrics,
                    source=source,
                )
            )
        elif event_type == "item_complete":
            out.append(
                ItemCompleteEvent(
                    ts=ts_value,
                    hierarchy=item_hierarchy,
                    metrics=metrics,
                    source=source,
                )
            )
        else:
            error = raw.get("error")
            failure_class = raw.get("failure_class")
            out.append(
                ItemFailEvent(
                    ts=ts_value,
                    hierarchy=item_hierarchy,
                    metrics=metrics,
                    source=source,
                    error=error if isinstance(error, str) else "",
                    failure_class=failure_class if isinstance(failure_class, str) else None,
                )
            )
    return out


def _hierarchy_from_process_event(raw: dict[str, object], fallback: HierarchyRef) -> HierarchyRef:
    subgraph_key = _str_or_none(raw.get("subgraph_key")) or _subgraph_key_from_process_node_id(
        _str_or_none(raw.get("process_node_id")) or fallback.process_node_id
    )
    process_id = (
        _str_or_none(raw.get("process_node_id"))
        or fallback.process_node_id
        or process_node_id(subgraph_key)
    )
    raw_step_node_id = _str_or_none(raw.get("step_node_id"))
    raw_step_id = _str_or_none(raw.get("step_id"))
    qualified_step_id = raw_step_node_id or (
        step_node_id(subgraph_key, raw_step_id) if raw_step_id else fallback.step_node_id
    )
    return HierarchyRef(
        run_id=fallback.run_id,
        process_node_id=process_id,
        step_node_id=qualified_step_id,
        item_key=_str_or_none(raw.get("item_key")) or fallback.item_key,
        worker_id=_str_or_none(raw.get("worker_id")) or fallback.worker_id,
        file_path=fallback.file_path,
        tool_name=fallback.tool_name,
    )


def _subgraph_key_from_process_node_id(value: str | None) -> str:
    if value and value.startswith("process:"):
        return value.removeprefix("process:")
    return ROOT_SUBGRAPH_KEY


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _runpool_events_from_log(
    log_events: list[LogEvent],
    hierarchy: HierarchyRef,
    source: SourceRef,
) -> list[ResourceEvent]:
    """Emit Sample + Billing events from runpool ``process_exit`` lines.

    Sample events always land (they capture wall_time_s + peak RSS for the
    worker). Billing events are conditional on the worker carrying enough
    resource metadata to invoke :func:`metaproc.cloud.gcp.billing.billable_for_span`
    — typically only true for cloud Batch runs that stamp ``machine_type``
    onto the runpool event.
    """
    out: list[ResourceEvent] = []
    for ev in log_events:
        if ev.adapter != "runpool" or not isinstance(ev.raw, dict):
            continue
        raw: dict[str, object] = ev.raw
        if raw.get("event") != "process_exit":
            continue

        elapsed_s = _float_or_none(raw.get("elapsed_s"))
        peak_rss = _int_or_none(raw.get("peak_rss_bytes"))
        ts_value = _ts_from_event(ev, source)

        out.append(
            SampleEvent(
                ts=ts_value,
                hierarchy=hierarchy,
                source=source,
                metrics=Metrics(
                    wall_time_s=elapsed_s,
                    local_compute_s=elapsed_s,
                    rss_bytes_max=peak_rss,
                ),
                taxonomy=TaxonomyPaths(
                    resource_path=["resource", "local", "runpool_process"],
                ),
            )
        )

        machine_type = raw.get("machine_type")
        cpu_milli = _int_or_none(raw.get("cpu_milli"))
        memory_mib = _int_or_none(raw.get("memory_mib"))
        if elapsed_s is None or (
            not isinstance(machine_type, str) and cpu_milli is None and memory_mib is None
        ):
            continue
        try:
            billable = billable_for_span(
                elapsed_s=elapsed_s,
                machine_type=machine_type if isinstance(machine_type, str) else None,
                cpu_milli=cpu_milli,
                memory_mib=memory_mib,
            )
        except ValueError:
            continue

        out.append(
            BillingEvent(
                ts=ts_value,
                hierarchy=hierarchy,
                source=source,
                metrics=Metrics(
                    billable_vm_hours=round(billable.vm_hours, 6),
                    billable_vcpu_hours=round(billable.vcpu_hours, 6),
                    billable_memory_gib_hours=round(billable.memory_gib_hours, 6),
                ),
                taxonomy=TaxonomyPaths(
                    resource_path=["resource", "cloud", "batch"],
                ),
            )
        )
    return out


def _ts_from_event(ev: LogEvent, source: SourceRef) -> datetime:
    if ev.timestamp:
        try:
            return datetime.fromisoformat(ev.timestamp)
        except ValueError:
            pass
    return _source_timestamp(source)


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _tool_call_event(span: ToolSpan, hierarchy: HierarchyRef, source: SourceRef) -> ResourceEvent:
    metrics = Metrics(
        tool_calls=1,
        tool_failures=1 if span.is_error else 0,
        tool_exec_s=span.duration_s,
    )
    taxonomy = TaxonomyPaths(tool_path=list(span.tool_path))
    span_hierarchy = hierarchy.model_copy(update={"tool_name": span.tool_name})
    return ToolCallEvent(
        ts=span.ended_at or span.started_at or _source_timestamp(source),
        span_id=_tool_span_id(span),
        hierarchy=span_hierarchy,
        metrics=metrics,
        taxonomy=taxonomy,
        source=source,
    )


def _wait_event(span: ThrottleSpan, hierarchy: HierarchyRef, source: SourceRef) -> ResourceEvent:
    metrics = Metrics(
        wait_throttling_s=span.duration_s,
        wait_rate_limit_s=span.duration_s
        if span.time_kind_path[1:2] == ("throttling",)
        and span.time_kind_path[2:3] == ("rate_limits",)
        else None,
        wait_budget_s=span.duration_s
        if span.time_kind_path[1:2] == ("throttling",) and span.time_kind_path[2:3] == ("budget",)
        else None,
    )
    taxonomy = TaxonomyPaths(
        time_kind_path=list(span.time_kind_path),
        provider_path=["provider", span.provider] if span.provider else None,
    )
    return WaitEvent(
        ts=span.ended_at or span.started_at or _source_timestamp(source),
        span_id=_throttle_span_id(span),
        hierarchy=hierarchy,
        metrics=metrics,
        taxonomy=taxonomy,
        source=source,
    )


def _usage_event_for_file(
    log_file: LogFile,
    hierarchy: HierarchyRef,
    source: SourceRef,
    *,
    authoritative_meter_keys: set[tuple[str, str, str, str]] | None = None,
) -> ResourceEvent | None:
    """Emit a session-level UsageEvent when the file carries terminal usage stats."""
    stats = log_file.usage_stats
    if stats is None:
        # Fall back to scalar tracking that LogFile collects before usage_stats lands.
        if log_file.cost_usd is None and log_file.input_tokens is None:
            return None
        metrics = Metrics(
            wall_time_s=log_file.duration_s,
            actual_cost_usd=log_file.cost_usd,
            input_tokens=log_file.input_tokens,
            output_tokens=log_file.output_tokens,
            tool_calls=log_file.tool_calls,
        )
        taxonomy = TaxonomyPaths(
            model_path=["model", log_file.model] if log_file.model else None,
        )
        return UsageEvent(
            ts=_source_timestamp(source),
            hierarchy=hierarchy,
            metrics=metrics,
            taxonomy=taxonomy,
            source=source,
        )

    metrics = Metrics(
        wall_time_s=stats.duration_s or None,
        input_tokens=stats.input_tokens or None,
        output_tokens=stats.output_tokens or None,
        cache_read_tokens=stats.cache_read_tokens or None,
        cache_write_tokens=stats.cache_write_tokens or None,
        tool_calls=stats.tool_calls or None,
    )
    if stats.cost_usd:
        if stats.cost_is_estimated:
            metrics.list_cost_usd = stats.cost_usd
        else:
            metrics.actual_cost_usd = stats.cost_usd
    elif stats.model:
        estimated_cost = compute_cost(stats, load_pricing())
        if estimated_cost:
            metrics.list_cost_usd = estimated_cost

    taxonomy = TaxonomyPaths(
        provider_path=["provider", stats.provider] if stats.provider else None,
        model_path=(
            ["model", stats.provider, stats.model]
            if stats.provider and stats.model
            else (["model", stats.model] if stats.model else None)
        ),
    )

    provider = (
        ProviderRef(
            provider=stats.provider,
            product="llm",
            model=stats.model or None,
        )
        if stats.provider
        else None
    )
    meters: list[MeteredQuantity] = []
    if provider is not None:
        request_key = MeterKey(
            provider=provider.provider,
            product=provider.product,
            meter="requests",
            unit="request",
        )
        if request_key.sort_key() not in (authoritative_meter_keys or set()):
            meters.append(
                MeteredQuantity(
                    key=request_key,
                    coverage=CoverageState.UNMEASURED,
                )
            )

    return UsageEvent(
        ts=_source_timestamp(source),
        hierarchy=hierarchy,
        metrics=metrics,
        provider=provider,
        meters=meters,
        taxonomy=taxonomy,
        source=source,
    )


def _provider_observation_event(
    observation: ProviderMeterObservation,
    hierarchy: HierarchyRef,
    source: SourceRef,
) -> ResourceEvent:
    """Project one sanitized provider observation into the common event stream."""
    return UsageEvent(
        event_id=observation.event_id,
        ts=observation.ts or _source_timestamp(source),
        span_id=observation.span_id,
        parent_span_id=observation.parent_span_id,
        hierarchy=hierarchy,
        metrics=Metrics(
            api_requests=observation.api_requests,
            api_failures=observation.api_failures,
            retries=observation.retries,
            cache_hits=observation.cache_hits,
            cache_misses=observation.cache_misses,
        ),
        provider=observation.provider,
        meters=observation.meters,
        taxonomy=TaxonomyPaths(
            provider_path=["provider", observation.provider.provider],
            model_path=(
                [
                    "model",
                    observation.provider.provider,
                    observation.provider.model,
                ]
                if observation.provider.model
                else None
            ),
        ),
        source=source,
    )


def _tool_span_id(span: ToolSpan) -> str:
    started = span.started_at.isoformat() if span.started_at else "0"
    return f"tool:{span.adapter}:{span.tool_name}:{started}"


def _throttle_span_id(span: ThrottleSpan) -> str:
    started = span.started_at.isoformat() if span.started_at else "0"
    return f"throttle:{span.provider or 'unknown'}:{started}"


def _source_timestamp(source: SourceRef) -> datetime:
    """Return a portable sentinel when a source record lacks an event timestamp."""
    _ = source
    return datetime.fromtimestamp(0, tz=UTC)
