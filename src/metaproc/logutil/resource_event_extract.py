"""Convert parsed LogEvents into typed `ResourceEvent` instances.

The roll-up builder needs a per-line evidence trail, not just file-level
usage totals. This module walks a parsed `LogFile` and its `LogEvent`
stream, then emits one typed `ResourceEvent` per attributable atom such as
tool calls, throttling windows, session usage, process step/item lifecycle,
local samples, and billing estimates.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from metaproc.cloud.gcp.billing import billable_for_span
from metaproc.logutil.agent_provider_meters import (
    AgentProviderEvidence,
    extract_agent_provider_evidence,
)
from metaproc.logutil.parsing import LogEvent, LogFile
from metaproc.logutil.throttling import ThrottleSpan, attribute_throttling
from metaproc.logutil.tool_failures import FailureKind
from metaproc.logutil.tool_spans import ToolSpan, pair_tool_events
from metaproc.logutil.usage import estimate_list_cost
from metaproc.models.node_ids import ROOT_SUBGRAPH_KEY, process_node_id, step_node_id
from metaproc.models.resources import (
    BillingEvent,
    HierarchyRef,
    ItemCompleteEvent,
    ItemFailEvent,
    ItemStartEvent,
    Metrics,
    ProviderRef,
    ResourceEvent,
    SampleEvent,
    SourceKind,
    SourceRef,
    StepCompleteEvent,
    StepFailEvent,
    StepStartEvent,
    TaxonomyPaths,
    ToolCallEvent,
    UsageEvent,
    WaitEvent,
)

_UNKNOWN_EVIDENCE_TS = datetime(1970, 1, 1, tzinfo=UTC)


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

    # Tool calls. Pair exactly once so terminal aggregates can contribute only
    # the positive residual beyond these canonical invocations.
    tool_spans = pair_tool_events(log_events)
    for tool_span in tool_spans:
        out.append(_tool_call_event(tool_span, hierarchy, base_source))

    # Throttling windows.
    for throttle_span in attribute_throttling(log_events):
        if throttle_span.duration_s is None or throttle_span.duration_s <= 0:
            # Allowed-warning markers are useful for evidence count but don't
            # contribute time; skip emitting wait events with zero duration to
            # keep the events file lean.
            continue
        out.append(_wait_event(throttle_span, hierarchy, base_source))

    # Session-level usage (one event per file when LogFile picked up usage_stats)
    adapter = log_file.parser.adapter_name if log_file.parser is not None else "unknown"
    stats_provider = log_file.usage_stats.provider if log_file.usage_stats is not None else None
    provider_evidence = extract_agent_provider_evidence(
        adapter=adapter,
        model=log_file.model,
        events=log_events,
        provider=stats_provider,
    )
    evidence_ts = _terminal_timestamp(log_events)
    usage_event = _usage_event_for_file(
        log_file,
        hierarchy,
        base_source,
        granular_tool_calls=len(tool_spans),
        provider=provider_evidence.provider if provider_evidence is not None else None,
        evidence_ts=evidence_ts,
    )
    if usage_event is not None:
        out.append(usage_event)
    if provider_evidence is not None:
        out.append(
            _provider_meter_event(
                provider_evidence,
                hierarchy,
                base_source,
                ts=evidence_ts,
            )
        )

    # Runpool process_exit → Sample (always) + Billing (when machine_type known)
    if source_kind == "runpool_events":
        out.extend(_runpool_events_from_log(log_events, hierarchy, base_source))

    if source_kind == "process_events":
        out.extend(_step_lifecycle_events_from_log(log_events, hierarchy, base_source))
        out.extend(_item_lifecycle_events_from_log(log_events, hierarchy, base_source))

    return out


def _step_lifecycle_events_from_log(
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
        if event_type not in ("step_start", "step_complete", "step_fail"):
            continue
        if not isinstance(raw.get("step_id"), str):
            continue

        step_hierarchy = _hierarchy_from_process_event(raw, hierarchy)
        elapsed_s = _float_or_none(raw.get("elapsed_s"))
        metrics = Metrics(wall_time_s=elapsed_s) if elapsed_s is not None else Metrics()
        ts_value = _ts_from_event(ev)

        if event_type == "step_start":
            out.append(
                StepStartEvent(
                    ts=ts_value,
                    hierarchy=step_hierarchy,
                    source=source,
                )
            )
        elif event_type == "step_complete":
            out.append(
                StepCompleteEvent(
                    ts=ts_value,
                    hierarchy=step_hierarchy,
                    metrics=metrics,
                    source=source,
                )
            )
        else:
            error = raw.get("error")
            out.append(
                StepFailEvent(
                    ts=ts_value,
                    hierarchy=step_hierarchy,
                    metrics=metrics,
                    source=source,
                    error=error if isinstance(error, str) else "",
                )
            )
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
        ts_value = _ts_from_event(ev)

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
        execution_profile=fallback.execution_profile,
        lane_id=fallback.lane_id,
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
        ts_value = _ts_from_event(ev)

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


def _ts_from_event(ev: LogEvent) -> datetime:
    if ev.timestamp:
        try:
            return datetime.fromisoformat(ev.timestamp)
        except ValueError:
            pass
    return _UNKNOWN_EVIDENCE_TS


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
        ts=span.ended_at or span.started_at or _UNKNOWN_EVIDENCE_TS,
        span_id=_tool_span_id(span),
        hierarchy=span_hierarchy,
        metrics=metrics,
        failure_kind=_tool_failure_kind(span),
        taxonomy=taxonomy,
        source=source,
    )


def _tool_failure_kind(span: ToolSpan) -> FailureKind | None:
    if span.failure_class == "unmatched_start":
        return FailureKind.ADAPTER_DROPPED_CALL
    if span.failure_class == "unmatched_result":
        return FailureKind.UNKNOWN
    if span.is_error:
        return FailureKind.TOOL_ERROR
    return None


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
        ts=span.ended_at or span.started_at or _UNKNOWN_EVIDENCE_TS,
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
    granular_tool_calls: int,
    provider: ProviderRef | None,
    evidence_ts: datetime,
) -> ResourceEvent | None:
    """Emit a session-level UsageEvent when the file carries terminal usage stats."""
    stats = log_file.usage_stats
    if stats is None:
        # Fall back to scalar tracking that LogFile collects before usage_stats lands.
        if log_file.cost_usd is None and log_file.input_tokens is None:
            return None
        metrics = Metrics(
            wall_time_s=log_file.duration_s,
            list_cost_usd=log_file.cost_usd,
            input_tokens=log_file.input_tokens,
            output_tokens=log_file.output_tokens,
            tool_calls=_tool_call_residual(log_file.tool_calls, granular_tool_calls),
        )
        taxonomy = TaxonomyPaths(
            model_path=["model", log_file.model] if log_file.model else None,
        )
        return UsageEvent(
            ts=evidence_ts,
            span_id="usage:terminal",
            hierarchy=hierarchy,
            metrics=metrics,
            provider=provider,
            taxonomy=taxonomy,
            source=source,
        )

    metrics = Metrics(
        wall_time_s=stats.duration_s or None,
        input_tokens=stats.input_tokens if stats.has_token_usage else None,
        output_tokens=stats.output_tokens if stats.has_token_usage else None,
        cache_read_tokens=stats.cache_read_tokens if stats.has_token_usage else None,
        cache_write_tokens=stats.cache_write_tokens if stats.has_token_usage else None,
        tool_calls=_tool_call_residual(
            stats.tool_calls if stats.has_tool_calls else None,
            granular_tool_calls,
        ),
    )
    metrics.list_cost_usd = estimate_list_cost(stats)

    taxonomy = TaxonomyPaths(
        provider_path=["provider", stats.provider] if stats.provider else None,
        model_path=(
            ["model", stats.provider, stats.model]
            if stats.provider and stats.model
            else (["model", stats.model] if stats.model else None)
        ),
    )

    return UsageEvent(
        ts=evidence_ts,
        span_id="usage:terminal",
        hierarchy=hierarchy,
        metrics=metrics,
        provider=provider,
        taxonomy=taxonomy,
        source=source,
    )


def _provider_meter_event(
    evidence: AgentProviderEvidence,
    hierarchy: HierarchyRef,
    source: SourceRef,
    *,
    ts: datetime,
) -> ResourceEvent:
    return UsageEvent(
        ts=ts,
        span_id=f"provider:{evidence.provider.provider}:{evidence.provider.product}",
        hierarchy=hierarchy,
        provider=evidence.provider,
        meters=list(evidence.meters),
        taxonomy=TaxonomyPaths(
            provider_path=["provider", evidence.provider.provider],
            model_path=(
                ["model", evidence.provider.provider, evidence.provider.model]
                if evidence.provider.model
                else None
            ),
        ),
        source=source,
    )


def _tool_span_id(span: ToolSpan) -> str:
    return f"tool:{span.adapter}:{span.invocation_id}"


def _throttle_span_id(span: ThrottleSpan) -> str:
    started = span.started_at.isoformat() if span.started_at else "0"
    return f"throttle:{span.provider or 'unknown'}:{started}"


def _tool_call_residual(total: int | None, granular: int) -> int | None:
    """Return the measured terminal residual beyond granular spans."""
    if total is None:
        return None
    return max(total - granular, 0)


def _terminal_timestamp(events: list[LogEvent]) -> datetime:
    for event in reversed(events):
        if event.is_done:
            return _ts_from_event(event)
    return _UNKNOWN_EVIDENCE_TS
