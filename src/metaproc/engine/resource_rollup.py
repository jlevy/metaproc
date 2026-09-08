"""Normalize local evidence and project the canonical resource ledger.

Source adapters produce typed ``ResourceEvent`` records. Reconciliation assigns
stable identities, deduplicates byte-equivalent evidence, and rejects conflicts.
Every hierarchy total, taxonomy rollup, provider meter, and budget evaluation is
then derived from that reconciled ledger; cached projections are never inputs.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import TypeAdapter
from strif import atomic_output_file

from metaproc.engine.resource_hierarchy import build_hierarchy_skeleton
from metaproc.engine.resource_reconciliation import (
    aggregate_meter_rollups,
    merge_meter_rollups,
    reconcile_resource_events,
)
from metaproc.io import iter_artifact_paths, logical_path
from metaproc.logutil.log_path_owner import (
    LogOwner,
    derive_owner,
    derive_owner_for_bundle,
    derive_owner_for_hierarchy,
)
from metaproc.logutil.parsing import (
    LogEvent,
    LogFile,
)
from metaproc.logutil.resource_event_extract import extract_resource_events
from metaproc.models.plan_bundle import PlanBundle
from metaproc.models.resource_budget import (
    ResourceBudgetSpec,
    ResourceFinalization,
    evaluate_resource_budgets,
)
from metaproc.models.resources import (
    RESOURCES_DOCUMENT_CONTRACT,
    HierarchyRef,
    LogSummary,
    Metrics,
    Node,
    PrefixRollup,
    ResourceEvent,
    ResourcesDocument,
    SourceKind,
    SourceLog,
    SourceRef,
    ToolCallEvent,
)
from metaproc.plugins.discovery import get_plugin_registry
from metaproc.stats.path_tally import PathTally, render_canonical

log = logging.getLogger(__name__)

# Field set on `Metrics` that participates in the bottom-up sum.
_ADDITIVE_METRIC_FIELDS = (
    "wall_time_s",
    "active_cpu_s",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "actual_cost_usd",
    "list_cost_usd",
    "tool_calls",
    "tool_failures",
    "wait_throttling_s",
    "wait_rate_limit_s",
    "wait_budget_s",
    "wait_network_s",
    "tool_exec_s",
    "local_compute_s",
    "api_requests",
    "api_failures",
    "retries",
    "cache_hits",
    "cache_misses",
    "billable_vm_hours",
    "billable_vcpu_hours",
    "billable_memory_gib_hours",
)

_MAX_METRIC_FIELDS = (
    "cpu_pct_max",
    "rss_bytes_max",
)

_AVERAGE_METRIC_FIELDS = (
    "cpu_pct_avg",
    "rss_bytes_avg",
)

_AverageSamples = dict[str, dict[str, tuple[float, int]]]


@dataclass
class ResourceBuildResult:
    """Artifacts produced by a resource roll-up build.

    ``document`` is the `ResourcesDocument` (the persisted ``resources.json``
    contract). ``events`` is the typed list backing
    ``resource-events.jsonl`` — the per-line evidence trail that lets
    operators drill from a roll-up number back to the originating log
    record. ``document_path`` and ``events_path`` are the on-disk targets
    `write_resource_artifacts` uses; callers that built without intent to
    persist can ignore them.
    """

    document: ResourcesDocument
    events: list[ResourceEvent]
    document_path: Path | None = None
    events_path: Path | None = None


_RESOURCE_EVENT_ADAPTER: TypeAdapter[ResourceEvent] = TypeAdapter(ResourceEvent)


def write_resource_artifacts(result: ResourceBuildResult) -> None:
    """Persist ``resources.json`` and ``resource-events.jsonl`` atomically.

    Uses :func:`strif.atomic_output_file` so a crashed run never leaves a
    half-written ``resources.json`` that future ``metaproc resource-report``
    invocations would refuse to parse. Both paths must be set on
    ``result``; the caller is expected to populate them via the
    ``write=True`` path on :func:`build_resource_artifacts` or by setting
    them explicitly before calling this helper.
    """
    if result.document_path is None or result.events_path is None:
        msg = "ResourceBuildResult.document_path and events_path must be set to persist"
        raise ValueError(msg)
    result.document_path.parent.mkdir(parents=True, exist_ok=True)
    result.events_path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_output_file(result.events_path) as tmp:
        tmp.write_text(
            "".join(
                _RESOURCE_EVENT_ADAPTER.dump_json(event).decode() + "\n" for event in result.events
            )
        )
    with atomic_output_file(result.document_path) as tmp:
        tmp.write_text(result.document.model_dump_json(by_alias=True, indent=2))


def build_resources_document(
    *,
    bundle: PlanBundle,
    run_dir: Path,
    run_id: str,
    extra_source_logs: Iterable[Path] | None = None,
    source_events_path: str = ".logs/resource-events.jsonl",
) -> ResourcesDocument:
    """Build just the `ResourcesDocument` from a `PlanBundle` and run dir.

    Convenience wrapper around :func:`build_resource_artifacts` for callers
    that don't need the per-line events list.
    """
    return build_resource_artifacts(
        bundle=bundle,
        run_dir=run_dir,
        run_id=run_id,
        extra_source_logs=extra_source_logs,
        source_events_path=source_events_path,
    ).document


def build_resource_artifacts(
    *,
    bundle: PlanBundle | None,
    run_dir: Path,
    run_id: str,
    hierarchy_root: Node | None = None,
    mapped_composite_step_ids: Collection[str] | None = None,
    extra_source_logs: Iterable[Path] | None = None,
    source_events_path: str = ".logs/resource-events.jsonl",
    write: bool = False,
    document_path: Path | None = None,
) -> ResourceBuildResult:
    """Build `ResourcesDocument` plus the underlying `ResourceEvent` list.

    ``extra_source_logs`` lets callers point at logs outside the standard
    ``run_dir/**/.logs/*.jsonl`` layout (e.g., ad-hoc fixtures); they are
    parsed identically to discovered files.

    Terminal recovery may pass ``bundle=None`` with an immutable
    ``hierarchy_root``. Ownership is then resolved from that hierarchy, so raw
    local logs remain usable even when the authored process files disappeared.

    When ``write=True`` the artifacts are persisted atomically via
    :func:`write_resource_artifacts`. ``document_path`` overrides the
    default ``run_dir/resources.json`` target.
    """
    if hierarchy_root is not None:
        root = hierarchy_root.model_copy(deep=True)
    elif bundle is not None:
        root = build_hierarchy_skeleton(bundle, run_id=run_id, run_dir=run_dir)
    else:
        root = Node(node_type="run", node_id=run_id, label=run_id)

    generated_events_path = run_dir / source_events_path
    resource_sources = get_plugin_registry().resource_event_sources
    external_paths = {path for source in resource_sources for path in source.discover(run_dir)}
    discovered = _discover_log_files(
        run_dir,
        exclude=[generated_events_path, *external_paths],
    )
    if extra_source_logs:
        discovered = list(discovered) + [Path(p) for p in extra_source_logs]
    discovered = _exclude_paths(sorted(set(discovered)), exclude=[generated_events_path])

    nodes_by_id = _index_nodes(root)
    source_logs: list[SourceLog] = []
    resource_events: list[ResourceEvent] = []

    for log_path in discovered:
        owner = _derive_log_owner(
            log_path,
            run_dir,
            bundle=bundle,
            hierarchy=root,
            mapped_composite_step_ids=mapped_composite_step_ids,
        )
        log_file, events = _parse_file(log_path)
        if log_file is None:
            continue

        kind: SourceKind = _kind_from_adapter(
            log_file.parser.adapter_name if log_file.parser else "unknown"
        )
        adapter = log_file.parser.adapter_name if log_file.parser else None

        owner_node_id = _resolve_owner_node_id(owner, nodes_by_id)
        owner_node = nodes_by_id.get(owner_node_id) if owner_node_id else None

        log_summary = _log_summary_for(log_file, events)

        if owner_node is None:
            root.log_summary = _merge_log_summary(root.log_summary, log_summary)
            root.source_refs.append(_source_ref(log_path, run_dir, kind))
        else:
            owner_node.log_summary = _merge_log_summary(owner_node.log_summary, log_summary)
            owner_node.source_refs.append(_source_ref(log_path, run_dir, kind))

        # Per-line typed event extraction. Events carry the deepest
        # hierarchy assignment we know for this log file; the router sends them
        # onto file/tool leaf nodes when the event names a file_path /
        # tool_name and accrues the metrics there.
        hierarchy_for_events = _hierarchy_for_log(run_id, owner, log_path, run_dir)
        try:
            stat = log_path.stat()
            size_bytes: int | None = stat.st_size
            mtime_ns: int | None = stat.st_mtime_ns
        except OSError:
            size_bytes, mtime_ns = None, None
        emitted = extract_resource_events(
            log_path=log_path,
            log_file=log_file,
            log_events=events,
            hierarchy=hierarchy_for_events,
            source_kind=kind,
            source_path=str(_relative_or_str(log_path, run_dir)),
            source_size_bytes=size_bytes,
            source_mtime_ns=mtime_ns,
        )
        resource_events.extend(emitted)

        source_logs.append(
            SourceLog(
                kind=kind,
                path=str(_relative_or_str(log_path, run_dir)),
                adapter=adapter,
                owner_node_id=owner_node_id,
                summary=log_summary,
            )
        )

    for source in resource_sources:
        for source_path in source.discover(run_dir):
            owner = _derive_log_owner(
                source_path,
                run_dir,
                bundle=bundle,
                hierarchy=root,
                mapped_composite_step_ids=mapped_composite_step_ids,
            )
            owner_node_id = _resolve_owner_node_id(owner, nodes_by_id)
            owner_node = nodes_by_id.get(owner_node_id) if owner_node_id else None
            try:
                stat = source_path.stat()
                source_size: int | None = stat.st_size
                source_mtime: int | None = stat.st_mtime_ns
            except OSError:
                source_size, source_mtime = None, None
            hierarchy = _hierarchy_for_log(run_id, owner, source_path, run_dir)
            events = list(
                source.extract(
                    log_path=source_path,
                    hierarchy=hierarchy,
                    source_path=str(_relative_or_str(source_path, run_dir)),
                    source_size_bytes=source_size,
                    source_mtime_ns=source_mtime,
                )
            )
            resource_events.extend(events)
            if events:
                tool_call_events = [event for event in events if isinstance(event, ToolCallEvent)]
                summary = LogSummary(
                    source_log_count=1,
                    event_count=len(events),
                    tool_call_count=len(tool_call_events),
                    tool_failure_count=sum(
                        1 for event in tool_call_events if event.metrics.tool_failures
                    ),
                )
                source_logs.append(
                    SourceLog(
                        kind=source.source_kind,
                        path=str(_relative_or_str(source_path, run_dir)),
                        adapter=source.adapter,
                        owner_node_id=owner_node_id,
                        summary=summary,
                    )
                )

    resource_events = reconcile_resource_events(resource_events)
    document = project_resource_document(
        hierarchy_root=root,
        run_id=run_id,
        events=resource_events,
        source_events_path=source_events_path,
        source_logs=source_logs,
    )
    result = ResourceBuildResult(
        document=document,
        events=resource_events,
        document_path=document_path or (run_dir / "resources.json"),
        events_path=run_dir / source_events_path,
    )
    if write:
        write_resource_artifacts(result)
    return result


def _derive_log_owner(
    log_path: Path,
    run_dir: Path,
    *,
    bundle: PlanBundle | None,
    hierarchy: Node,
    mapped_composite_step_ids: Collection[str] | None,
) -> LogOwner:
    if bundle is not None:
        return derive_owner_for_bundle(log_path, run_dir, bundle)
    if _root_process_node(hierarchy) is not None:
        return derive_owner_for_hierarchy(
            log_path,
            run_dir,
            hierarchy,
            mapped_composite_step_ids=mapped_composite_step_ids,
        )
    return derive_owner(log_path, run_dir)


def _root_process_node(root: Node) -> Node | None:
    if root.node_type == "process":
        return root
    return next((child for child in root.children if child.node_type == "process"), None)


def project_resource_document(
    *,
    hierarchy_root: Node,
    run_id: str,
    events: Sequence[ResourceEvent],
    source_events_path: str = ".logs/resource-events.jsonl",
    source_logs: Sequence[SourceLog] = (),
    generated_at: datetime | None = None,
    budgets: Sequence[ResourceBudgetSpec] = (),
    finalization: ResourceFinalization | None = None,
    summary_path: str | None = None,
) -> ResourcesDocument:
    """Project one current document solely from the reconciled event ledger.

    Any metrics already present on ``hierarchy_root`` are discarded. This is
    intentional: terminal finalization and recovery must never reuse cached
    totals from a prior ``resources.json`` projection.
    """
    root = hierarchy_root.model_copy(deep=True)
    _clear_projection(root)
    nodes_by_id = _index_nodes(root)
    tallies: _TaxonomyTallies = {}
    average_samples: _AverageSamples = {}
    reconciled = reconcile_resource_events(events)

    for event in reconciled:
        leaf = _ensure_event_leaf(
            nodes_by_id=nodes_by_id,
            root=root,
            event=event,
            fallback_owner=None,
        )
        _add_metrics_into(leaf.self_metrics, event.metrics)
        _record_average_samples(average_samples, leaf, event.metrics)
        if event.meters:
            leaf.self_meters = merge_meter_rollups(
                [leaf.self_meters, aggregate_meter_rollups([event])]
            )
        _tally_event_taxonomy(tallies, event)

    _apply_self_averages(root, average_samples)
    _propagate_totals(root, average_samples)
    meter_rollups = root.total_meters
    coverage_gaps = [
        rollup.key for rollup in meter_rollups if rollup.coverage.value == "unmeasured"
    ]
    document = ResourcesDocument(
        schema=RESOURCES_DOCUMENT_CONTRACT,
        run_id=run_id,
        generated_at=generated_at or datetime.now(UTC),
        source_events_path=source_events_path,
        hierarchy_root=root,
        taxonomy_rollups=_persist_tally_as_prefix_rollups(tallies),
        source_logs=list(source_logs),
        unattributed=Metrics(),
        meter_rollups=meter_rollups,
        coverage_gaps=coverage_gaps,
        finalization=finalization,
        summary_path=summary_path,
    )
    document.budget_evaluations = evaluate_resource_budgets(
        document,
        budgets,
        events=reconciled,
    )
    return document


def _clear_projection(node: Node) -> None:
    node.self_metrics = Metrics()
    node.total_metrics = Metrics()
    node.self_meters = []
    node.total_meters = []
    for child in node.children:
        _clear_projection(child)


def _hierarchy_for_log(run_id: str, owner: LogOwner, log_path: Path, run_dir: Path) -> HierarchyRef:
    """Build a `HierarchyRef` for events emitted from this log file."""
    return HierarchyRef(
        run_id=run_id,
        process_node_id=owner.process_node_id,
        step_node_id=owner.step_node_id,
        item_key=owner.item_key,
        worker_id=None,
        file_path=str(_relative_or_str(log_path, run_dir)),
    )


# ── Discovery / parsing ────────────────────────────────────────────


def _discover_log_files(run_dir: Path, *, exclude: Iterable[Path] = ()) -> list[Path]:
    if not run_dir.exists():
        return []
    excluded = {_normalized_path(p) for p in exclude}
    return [
        p for p in iter_artifact_paths(run_dir, "**/*.jsonl") if _normalized_path(p) not in excluded
    ]


def _exclude_paths(paths: Iterable[Path], *, exclude: Iterable[Path]) -> list[Path]:
    excluded = {_normalized_path(p) for p in exclude}
    return [p for p in paths if _normalized_path(p) not in excluded]


def _normalized_path(path: Path) -> str:
    return str(logical_path(path).resolve())


def _parse_file(log_path: Path) -> tuple[LogFile | None, list[LogEvent]]:
    try:
        log_file = LogFile(log_path, color_idx=0)
        events = log_file.read_new_events()
        if log_file.done:
            events = list(events) + list(log_file.flush())
    except OSError:
        log.debug("Failed to read log file %s", log_path, exc_info=True)
        return None, []
    return log_file, events


def _kind_from_adapter(adapter_name: str) -> SourceKind:
    if adapter_name == "process":
        return "process_events"
    if adapter_name == "runpool":
        return "runpool_events"
    return "agent_log"


# ── Event → leaf routing ───────────────────────────────────────────


def _ensure_event_leaf(
    *,
    nodes_by_id: dict[str, Node],
    root: Node,
    event: ResourceEvent,
    fallback_owner: Node | None,
) -> Node:
    """Find or create the deepest hierarchy node that owns ``event``.

    Walks the chain ``process → step → item → file → tool``, materializing
    ``item:``, ``file:``, and ``tool:`` leaves on demand so per-event metrics
    land at the highest-resolution node we have evidence for. Falls back to
    ``fallback_owner`` (typically the log file's owner) when the event's
    hierarchy doesn't match a known node.
    """
    hierarchy = event.hierarchy
    step_node: Node | None = (
        nodes_by_id.get(hierarchy.step_node_id) if hierarchy.step_node_id else None
    )

    base_owner: Node | None
    if step_node is not None and hierarchy.item_key and hierarchy.step_node_id:
        # Materialize a per-item node on demand. The skeleton only fills
        # these in when status.yaml files exist, but per-event evidence
        # is also a strong signal that the item ran.
        base_owner = _ensure_item_node(
            nodes_by_id,
            step_node,
            hierarchy.step_node_id,
            hierarchy.item_key,
        )
    elif step_node is not None:
        base_owner = step_node
    elif hierarchy.process_node_id:
        base_owner = nodes_by_id.get(hierarchy.process_node_id)
    else:
        base_owner = None
    if base_owner is None:
        base_owner = fallback_owner if fallback_owner is not None else root

    leaf = base_owner
    if hierarchy.file_path:
        leaf = _ensure_file_node(nodes_by_id, leaf, hierarchy.file_path)
        if hierarchy.tool_name:
            leaf = _ensure_tool_node(nodes_by_id, leaf, hierarchy.file_path, hierarchy.tool_name)
    return leaf


def _ensure_item_node(
    nodes_by_id: dict[str, Node],
    step: Node,
    step_node_id: str,
    item_key: str,
) -> Node:
    node_id = f"{step_node_id}::{item_key}"
    existing = nodes_by_id.get(node_id)
    if existing is not None:
        return existing
    node = Node(
        node_type="item",
        node_id=node_id,
        label=item_key,
        parent_id=step.node_id,
    )
    step.children.append(node)
    nodes_by_id[node_id] = node
    return node


def _ensure_file_node(nodes_by_id: dict[str, Node], parent: Node, file_path: str) -> Node:
    node_id = f"file:{file_path}"
    existing = nodes_by_id.get(node_id)
    if existing is not None:
        return existing
    node = Node(
        node_type="file",
        node_id=node_id,
        label=Path(file_path).name,
        parent_id=parent.node_id,
    )
    parent.children.append(node)
    nodes_by_id[node_id] = node
    return node


def _ensure_tool_node(
    nodes_by_id: dict[str, Node],
    parent: Node,
    file_path: str,
    tool_name: str,
) -> Node:
    node_id = f"tool:{file_path}:{tool_name}"
    existing = nodes_by_id.get(node_id)
    if existing is not None:
        return existing
    node = Node(
        node_type="tool",
        node_id=node_id,
        label=tool_name,
        parent_id=parent.node_id,
    )
    parent.children.append(node)
    nodes_by_id[node_id] = node
    return node


# ── Node lookup / ownership ────────────────────────────────────────


def _index_nodes(root: Node) -> dict[str, Node]:
    out: dict[str, Node] = {}
    _walk(root, out)
    return out


def _walk(node: Node, out: dict[str, Node]) -> None:
    out[node.node_id] = node
    for child in node.children:
        _walk(child, out)


def _resolve_owner_node_id(owner: LogOwner, nodes: dict[str, Node]) -> str | None:
    """Pick the deepest node id from the skeleton that the owner matches."""
    if owner.step_node_id and owner.item_key:
        candidate = f"{owner.step_node_id}::{owner.item_key}"
        if candidate in nodes:
            return candidate
    if owner.step_node_id and owner.step_node_id in nodes:
        return owner.step_node_id
    if owner.process_node_id and owner.process_node_id in nodes:
        return owner.process_node_id
    return None


# ── Taxonomy tally ─────────────────────────────────────────────────


_TaxonomyTallies = dict[str, PathTally[float]]


def _tally_event_taxonomy(tallies: _TaxonomyTallies, event: ResourceEvent) -> None:
    """Derive taxonomy totals from one canonical ledger event."""
    taxonomy = event.taxonomy
    metrics = event.metrics
    exec_s = metrics.tool_exec_s
    wait_s = metrics.wait_throttling_s
    if wait_s is None:
        waits = (
            metrics.wait_rate_limit_s,
            metrics.wait_budget_s,
            metrics.wait_network_s,
        )
        wait_s = (
            sum(value for value in waits if value is not None)
            if any(value is not None for value in waits)
            else None
        )

    if taxonomy.tool_path and exec_s is not None:
        _add_taxonomy(tallies, "tool_exec_s", "tool_path", taxonomy.tool_path, exec_s)
    if taxonomy.provider_path:
        if metrics.actual_cost_usd is not None:
            _add_taxonomy(
                tallies,
                "actual_cost_usd",
                "provider_path",
                taxonomy.provider_path,
                metrics.actual_cost_usd,
            )
        if metrics.list_cost_usd is not None:
            _add_taxonomy(
                tallies,
                "list_cost_usd",
                "provider_path",
                taxonomy.provider_path,
                metrics.list_cost_usd,
            )
        if metrics.actual_cost_usd is None and metrics.list_cost_usd is None:
            if exec_s is not None:
                _add_taxonomy(
                    tallies, "tool_exec_s", "provider_path", taxonomy.provider_path, exec_s
                )
            elif wait_s is not None:
                _add_taxonomy(
                    tallies,
                    "wait_throttling_s",
                    "provider_path",
                    taxonomy.provider_path,
                    wait_s,
                )
    if taxonomy.model_path:
        for field_name in ("actual_cost_usd", "list_cost_usd"):
            value = cast(float | None, getattr(metrics, field_name))
            if value is not None:
                _add_taxonomy(tallies, field_name, "model_path", taxonomy.model_path, value)
    if taxonomy.time_kind_path:
        if wait_s is not None:
            _add_taxonomy(
                tallies,
                "wait_throttling_s",
                "time_kind_path",
                taxonomy.time_kind_path,
                wait_s,
            )
        elif exec_s is not None:
            _add_taxonomy(tallies, "tool_exec_s", "time_kind_path", taxonomy.time_kind_path, exec_s)
    if taxonomy.cost_kind_path:
        for field_name in ("actual_cost_usd", "list_cost_usd"):
            value = cast(float | None, getattr(metrics, field_name))
            if value is not None:
                _add_taxonomy(tallies, field_name, "cost_kind_path", taxonomy.cost_kind_path, value)
    if taxonomy.policy_path and exec_s is not None:
        _add_taxonomy(tallies, "tool_exec_s", "policy_path", taxonomy.policy_path, exec_s)
    if exec_s is not None:
        for family, path in taxonomy.extra_paths.items():
            if path:
                _add_taxonomy(
                    tallies,
                    "tool_exec_s",
                    f"{family}_path",
                    [family, *path],
                    exec_s,
                )


def _add_taxonomy(
    tallies: _TaxonomyTallies,
    metric_field: str,
    family: str,
    path: Sequence[str],
    value: float,
) -> None:
    tallies.setdefault(metric_field, PathTally[float]()).add(family, tuple(path), value)


def _persist_tally_as_prefix_rollups(
    tallies: _TaxonomyTallies,
) -> dict[str, list[PrefixRollup]]:
    """Turn the tuple-keyed tally into JSON-friendly `PrefixRollup` lists."""
    rows: dict[str, dict[tuple[str, ...], Metrics]] = {}
    for metric_field, tally in tallies.items():
        for family in tally.families():
            family_rows = rows.setdefault(family, {})
            for prefix, value in tally.iter_prefixes(family):
                metrics = family_rows.setdefault(prefix, Metrics())
                current = cast(float | None, getattr(metrics, metric_field))
                setattr(metrics, metric_field, (current or 0.0) + value)
    return {
        family: [
            PrefixRollup(
                path=list(prefix),
                canonical=render_canonical(prefix),
                metrics=metrics,
            )
            for prefix, metrics in sorted(family_rows.items())
        ]
        for family, family_rows in sorted(rows.items())
    }


# ── Bottom-up totals ───────────────────────────────────────────────


def _propagate_totals(node: Node, average_samples: _AverageSamples) -> dict[str, int]:
    """Compute totals and return raw sample counts for weighted averages."""
    child_counts: list[tuple[Node, dict[str, int]]] = []
    for child in node.children:
        child_counts.append((child, _propagate_totals(child, average_samples)))

    total = Metrics()
    _add_metrics_into(total, node.self_metrics)
    for child, _counts in child_counts:
        _add_metrics_into(total, child.total_metrics)
    total_average_counts: dict[str, int] = {}
    for field_name in _AVERAGE_METRIC_FIELDS:
        own_count = average_samples.get(node.node_id, {}).get(field_name, (0.0, 0))[1]
        own_value = cast(float | None, getattr(node.self_metrics, field_name))
        weighted_sum = (own_value or 0.0) * own_count
        count = own_count
        for child, counts in child_counts:
            child_count = counts.get(field_name, 0)
            child_value = cast(float | None, getattr(child.total_metrics, field_name))
            weighted_sum += (child_value or 0.0) * child_count
            count += child_count
        if count:
            setattr(total, field_name, weighted_sum / count)
            total_average_counts[field_name] = count
    node.total_metrics = total
    node.total_meters = merge_meter_rollups(
        [node.self_meters, *(child.total_meters for child in node.children)]
    )
    return total_average_counts


def _record_average_samples(samples: _AverageSamples, node: Node, metrics: Metrics) -> None:
    node_samples = samples.setdefault(node.node_id, {})
    for field_name in _AVERAGE_METRIC_FIELDS:
        value = cast(float | None, getattr(metrics, field_name))
        if value is None:
            continue
        total, count = node_samples.get(field_name, (0.0, 0))
        node_samples[field_name] = (total + value, count + 1)


def _apply_self_averages(node: Node, samples: _AverageSamples) -> None:
    for field_name, (total, count) in samples.get(node.node_id, {}).items():
        setattr(node.self_metrics, field_name, total / count)
    for child in node.children:
        _apply_self_averages(child, samples)


def _add_metrics_into(target: Metrics, source: Metrics) -> None:
    """Merge additive and peak fields from ``source`` with null discipline."""
    for field_name in _ADDITIVE_METRIC_FIELDS:
        src_value = cast(float | int | None, getattr(source, field_name))
        if src_value is None:
            continue
        current = cast(float | int | None, getattr(target, field_name))
        new_value = (current or 0) + src_value
        setattr(target, field_name, new_value)
    for field_name in _MAX_METRIC_FIELDS:
        src_value = cast(float | int | None, getattr(source, field_name))
        if src_value is None:
            continue
        current = cast(float | int | None, getattr(target, field_name))
        setattr(target, field_name, src_value if current is None else max(current, src_value))


# ── Source-log helpers ─────────────────────────────────────────────


def _log_summary_for(log_file: LogFile, events: list[LogEvent]) -> LogSummary:
    error_count = sum(1 for ev in events if ev.is_error)
    tool_calls = sum(1 for ev in events if ev.kind == "tool_call")
    tool_failures = sum(1 for ev in events if ev.kind == "tool_result" and ev.is_error)
    timestamps = [_parse_ts(ev.timestamp) for ev in events]
    real_ts = [t for t in timestamps if t is not None]
    return LogSummary(
        source_log_count=1,
        event_count=len(events),
        error_count=error_count,
        tool_call_count=tool_calls,
        tool_failure_count=tool_failures,
        first_ts=min(real_ts) if real_ts else None,
        last_ts=max(real_ts) if real_ts else None,
    )


def _merge_log_summary(into: LogSummary, addition: LogSummary) -> LogSummary:
    return LogSummary(
        source_log_count=into.source_log_count + addition.source_log_count,
        event_count=into.event_count + addition.event_count,
        error_count=into.error_count + addition.error_count,
        tool_call_count=into.tool_call_count + addition.tool_call_count,
        tool_failure_count=into.tool_failure_count + addition.tool_failure_count,
        first_ts=_min_ts(into.first_ts, addition.first_ts),
        last_ts=_max_ts(into.last_ts, addition.last_ts),
    )


def _source_ref(log_path: Path, run_dir: Path, kind: SourceKind) -> SourceRef:
    try:
        file_stat = log_path.stat()
        size = file_stat.st_size
        mtime_ns = file_stat.st_mtime_ns
    except OSError:
        size, mtime_ns = None, None
    return SourceRef(
        kind=kind,
        path=str(_relative_or_str(log_path, run_dir)),
        line_offset=None,
        size_bytes=size,
        mtime_ns=mtime_ns,
    )


def _relative_or_str(path: Path, run_dir: Path) -> Path | str:
    try:
        return path.resolve().relative_to(run_dir.resolve())
    except (ValueError, OSError):
        return str(path)


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _min_ts(a: datetime | None, b: datetime | None) -> datetime | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def _max_ts(a: datetime | None, b: datetime | None) -> datetime | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)
