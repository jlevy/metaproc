"""Roll up evidence from a run dir into a `ResourcesDocument`.

This builder joins structural, execution, and usage evidence into the typed
`ResourcesDocument` contract that the CLI and `/api/resources` serve.

Current builder scope:

- **Structural skeleton** — call `build_hierarchy_skeleton` to get the
  run → process → step → item tree.
- **Per-leaf evidence** — for every source log under the run dir, parse
  via the existing adapter parsers, derive plan-aware ownership, and
  aggregate tokens / cost / duration / tool calls into `self_metrics` on
  the deepest matching node.
- **Tool span pairing** — feed each session's events through
  `pair_tool_events` to build per-tool counts; classify durations by
  taxonomy path on `tool_path`.
- **Throttling attribution** — feed each session's events through
  `attribute_throttling`; accrue durations to
  `wait_throttling_s` / `wait_rate_limit_s` / `wait_budget_s` as
  appropriate.
- **Bottom-up totals** — recurse through the skeleton, compute
  ``total_metrics = self_metrics + sum(child.total_metrics)`` for each
  field, additive nulls behave as missing-evidence.
- **Taxonomy rollups** — drive a `PathTally` from each evidence span
  for `time_kind_path`, `provider_path`, `model_path`, `tool_path`;
  emit JSON-friendly `PrefixRollup` lists.
- **Source log inventory** — every parsed file yields a `SourceLog`
  with adapter, owner, and an event count summary so the browser can
  show "evidence behind this node" without rereading raw files.

Still intentionally limited:

- The runpool event reader is consulted only to enumerate worker
  lifetimes for billable-hours approximation; no charts.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import TypeAdapter
from strif import atomic_output_file

from metaproc.engine.resource_hierarchy import build_hierarchy_skeleton
from metaproc.ids import new_typed_id, require_typed_id
from metaproc.io import iter_artifact_paths, logical_path
from metaproc.logutil.log_path_owner import LogOwner, derive_owner_for_bundle
from metaproc.logutil.parsing import (
    LogEvent,
    LogFile,
)
from metaproc.logutil.resource_event_extract import extract_resource_events
from metaproc.logutil.throttling import (
    ThrottleSpan,
    attribute_throttling,
)
from metaproc.logutil.tool_spans import ToolSpan, pair_tool_events
from metaproc.models.plan_bundle import PlanBundle
from metaproc.models.resources import (
    SCHEMA_V2,
    CoverageState,
    HierarchyRef,
    LogSummary,
    MeteredQuantity,
    MeterKey,
    MeterRollup,
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

_EVENT_ID_DIGEST_BYTES = 20


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
    bundle: PlanBundle,
    run_dir: Path,
    run_id: str,
    extra_source_logs: Iterable[Path] | None = None,
    source_events_path: str = ".logs/resource-events.jsonl",
    write: bool = False,
    document_path: Path | None = None,
) -> ResourceBuildResult:
    """Build `ResourcesDocument` plus the underlying `ResourceEvent` list.

    ``extra_source_logs`` lets callers point at logs outside the standard
    ``run_dir/**/.logs/*.jsonl`` layout (e.g., ad-hoc fixtures); they are
    parsed identically to discovered files.

    When ``write=True`` the artifacts are persisted atomically via
    :func:`write_resource_artifacts`. ``document_path`` overrides the
    default ``run_dir/resources.json`` target.
    """
    root = build_hierarchy_skeleton(bundle, run_id=run_id, run_dir=run_dir)

    generated_events_path = run_dir / source_events_path
    resource_sources = get_plugin_registry().resource_event_sources
    provider_meter_sources = get_plugin_registry().provider_meter_sources
    external_paths = {path for source in resource_sources for path in source.discover(run_dir)}
    discovered = _discover_log_files(
        run_dir,
        exclude=[generated_events_path, *external_paths],
    )
    if extra_source_logs:
        discovered = list(discovered) + [Path(p) for p in extra_source_logs]
    discovered = _exclude_paths(sorted(set(discovered)), exclude=[generated_events_path])

    nodes_by_id = _index_nodes(root)
    tally = PathTally[float]()
    source_logs: list[SourceLog] = []
    resource_events: list[ResourceEvent] = []
    seen_events: dict[str, str] = {}
    unattributed = Metrics()

    for log_path in discovered:
        owner = derive_owner_for_bundle(log_path, run_dir, bundle)
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
            _accrue_unattributed(unattributed, log_file)
        else:
            owner_node.log_summary = _merge_log_summary(owner_node.log_summary, log_summary)
            owner_node.source_refs.append(_source_ref(log_path, run_dir, kind))

        # Tally taxonomy paths from spans (separate from per-node accrual,
        # which now happens via per-event routing below).
        tool_spans = pair_tool_events(events)
        throttle_spans = attribute_throttling(events)
        _accrue_taxonomy(
            tally,
            log_file=log_file,
            tool_spans=tool_spans,
            throttle_spans=throttle_spans,
        )

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
            provider_meter_sources=provider_meter_sources,
        )
        for raw_event in emitted:
            event = _reconcile_resource_event(raw_event, seen_events)
            if event is None:
                continue
            leaf = _ensure_event_leaf(
                nodes_by_id=nodes_by_id,
                root=root,
                event=event,
                fallback_owner=owner_node,
            )
            _add_metrics_into(leaf.self_metrics, event.metrics)
            _add_meter_rollups_into(
                leaf.self_meters,
                aggregate_meter_rollups([(event.event_id or "", event.meters)]),
            )
            resource_events.append(event)

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
            owner = derive_owner_for_bundle(source_path, run_dir, bundle)
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
            accepted_events: list[ResourceEvent] = []
            for raw_event in events:
                event = _reconcile_resource_event(raw_event, seen_events)
                if event is None:
                    continue
                leaf = _ensure_event_leaf(
                    nodes_by_id=nodes_by_id,
                    root=root,
                    event=event,
                    fallback_owner=owner_node,
                )
                _add_metrics_into(leaf.self_metrics, event.metrics)
                _add_meter_rollups_into(
                    leaf.self_meters,
                    aggregate_meter_rollups([(event.event_id or "", event.meters)]),
                )
                _tally_external_event_taxonomy(tally, event)
                accepted_events.append(event)
            resource_events.extend(accepted_events)
            if accepted_events:
                tool_call_events = [
                    event for event in accepted_events if isinstance(event, ToolCallEvent)
                ]
                summary = LogSummary(
                    source_log_count=1,
                    event_count=len(accepted_events),
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

    _propagate_totals(root)
    rollups = _persist_tally_as_prefix_rollups(tally)

    document = ResourcesDocument.model_validate(
        {
            "schema": SCHEMA_V2,
            "run_id": run_id,
            "generated_at": datetime.now(UTC),
            "source_events_path": source_events_path,
            "hierarchy_root": root.model_dump(),
            "taxonomy_rollups": {
                family: [r.model_dump() for r in entries] for family, entries in rollups.items()
            },
            "source_logs": [sl.model_dump() for sl in source_logs],
            "unattributed": unattributed.model_dump(),
            "meter_rollups": [rollup.model_dump() for rollup in root.total_meters],
            "unattributed_meters": [],
        }
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


# ── Self-metric accrual ────────────────────────────────────────────


def _accrue_into_self(self_metrics: Metrics, log_file: LogFile) -> None:
    stats = log_file.usage_stats
    if stats is None:
        # Even without typed usage stats, we can still pick up duration / cost
        # from the file's tracked attributes.
        if log_file.duration_s is not None:
            self_metrics.wall_time_s = (self_metrics.wall_time_s or 0.0) + log_file.duration_s
        if log_file.cost_usd is not None:
            self_metrics.actual_cost_usd = (self_metrics.actual_cost_usd or 0.0) + log_file.cost_usd
        return

    if stats.input_tokens:
        self_metrics.input_tokens = (self_metrics.input_tokens or 0) + stats.input_tokens
    if stats.output_tokens:
        self_metrics.output_tokens = (self_metrics.output_tokens or 0) + stats.output_tokens
    if stats.cache_read_tokens:
        self_metrics.cache_read_tokens = (
            self_metrics.cache_read_tokens or 0
        ) + stats.cache_read_tokens
    if stats.cache_write_tokens:
        self_metrics.cache_write_tokens = (
            self_metrics.cache_write_tokens or 0
        ) + stats.cache_write_tokens
    if stats.duration_s:
        self_metrics.wall_time_s = (self_metrics.wall_time_s or 0.0) + stats.duration_s
    if stats.cost_usd:
        if stats.cost_is_estimated:
            self_metrics.list_cost_usd = (self_metrics.list_cost_usd or 0.0) + stats.cost_usd
        else:
            self_metrics.actual_cost_usd = (self_metrics.actual_cost_usd or 0.0) + stats.cost_usd
    if stats.tool_calls:
        self_metrics.tool_calls = (self_metrics.tool_calls or 0) + stats.tool_calls


def _accrue_unattributed(unattributed: Metrics, log_file: LogFile) -> None:
    """Capture metrics from a log we couldn't pin to a hierarchy node.

    The per-event router handles attributable evidence; this is the
    fallback for log files whose owner can't be resolved (e.g., paths
    outside the run dir).
    """
    _accrue_into_self(unattributed, log_file)


# ── Taxonomy tally ─────────────────────────────────────────────────


def _tally_external_event_taxonomy(tally: PathTally[float], event: ResourceEvent) -> None:
    """Feed a plugin-emitted ResourceEvent's taxonomy paths into the PathTally.

    Arena (post-Strand C) stamps every event with the full TaxonomyPaths
    envelope; the rollup builder needs to tally each populated family on
    its own metric so per-tier / per-provider / per-tool roll-ups appear in
    `resource-usage.json`. Routing key:

    - tool_path tallies on tool_exec_s (matches the existing ToolSpan behavior)
    - provider_path / model_path tally on actual_cost_usd when present,
      otherwise fall back to wait/exec time so the family is at least visible
    - wait_* metrics tally on the corresponding wait duration
    - policy_path and extra_paths.mode tally on tool_exec_s so per-tier and
      per-mode breakdowns roll up across all attempts
    """
    taxonomy = event.taxonomy
    metrics = event.metrics
    exec_s = metrics.tool_exec_s or 0.0
    cost = metrics.actual_cost_usd or metrics.list_cost_usd or 0.0
    rate_limit_s = metrics.wait_rate_limit_s or 0.0
    throttle_s = metrics.wait_throttling_s or 0.0
    wait_total = rate_limit_s + throttle_s

    if taxonomy.tool_path and exec_s:
        tally.add("tool_path", tuple(taxonomy.tool_path), exec_s)
    if taxonomy.provider_path:
        amount = cost or (exec_s or wait_total)
        if amount:
            tally.add("provider_path", tuple(taxonomy.provider_path), amount)
    if taxonomy.model_path and cost:
        tally.add("model_path", tuple(taxonomy.model_path), cost)
    if taxonomy.time_kind_path and (wait_total or exec_s):
        amount = wait_total or exec_s
        tally.add("time_kind_path", tuple(taxonomy.time_kind_path), amount)
    if taxonomy.cost_kind_path and cost:
        tally.add("cost_kind_path", tuple(taxonomy.cost_kind_path), cost)
    if taxonomy.policy_path and exec_s:
        tally.add("policy_path", tuple(taxonomy.policy_path), exec_s)
    if exec_s:
        for family, path in taxonomy.extra_paths.items():
            if path:
                tally.add(f"{family}_path", (family, *path), exec_s)


def _accrue_taxonomy(
    tally: PathTally[float],
    *,
    log_file: LogFile,
    tool_spans: list[ToolSpan],
    throttle_spans: list[ThrottleSpan],
) -> None:
    for span in tool_spans:
        if span.duration_s is not None:
            tally.add("tool_path", span.tool_path, span.duration_s)
    for span in throttle_spans:
        if span.duration_s is not None and span.duration_s > 0:
            tally.add("time_kind_path", span.time_kind_path, span.duration_s)
            if span.provider:
                tally.add("provider_path", ("provider", span.provider), span.duration_s)

    stats = log_file.usage_stats
    if stats is None:
        return
    cost = stats.cost_usd
    if cost:
        if stats.provider:
            tally.add("provider_path", ("provider", stats.provider), float(cost))
        if stats.model:
            tally.add("model_path", ("model", stats.model), float(cost))


def _persist_tally_as_prefix_rollups(
    tally: PathTally[float],
) -> dict[str, list[PrefixRollup]]:
    """Turn the tuple-keyed tally into JSON-friendly `PrefixRollup` lists."""
    out: dict[str, list[PrefixRollup]] = {}
    for family in tally.families():
        rolls: list[PrefixRollup] = []
        for prefix, value in tally.iter_prefixes(family):
            metrics = _metrics_for_family(family, value)
            rolls.append(
                PrefixRollup(
                    path=list(prefix),
                    canonical=render_canonical(prefix),
                    metrics=metrics,
                )
            )
        out[family] = rolls
    return out


def _metrics_for_family(family: str, value: float) -> Metrics:
    """Map a tally value onto the most appropriate Metrics field for its family."""
    if family == "tool_path":
        return Metrics(tool_exec_s=value)
    if family == "time_kind_path":
        return Metrics(wait_throttling_s=value)
    if family in ("provider_path", "model_path", "cost_kind_path"):
        return Metrics(actual_cost_usd=value)
    if family == "policy_path" or family.endswith("_path"):
        return Metrics(tool_exec_s=value)
    raise ValueError(f"unknown metric family: {family}")


# ── Bottom-up totals ───────────────────────────────────────────────


def _propagate_totals(node: Node) -> None:
    """Recursively compute ``total_metrics = self_metrics + sum(child.total_metrics)``."""
    for child in node.children:
        _propagate_totals(child)

    total = Metrics()
    # Start from self_metrics
    _add_metrics_into(total, node.self_metrics)
    for child in node.children:
        _add_metrics_into(total, child.total_metrics)
    node.total_metrics = total
    total_meters: list[MeterRollup] = []
    _add_meter_rollups_into(total_meters, node.self_meters)
    for child in node.children:
        _add_meter_rollups_into(total_meters, child.total_meters)
    node.total_meters = total_meters


def _add_metrics_into(target: Metrics, source: Metrics) -> None:
    """In-place add every additive field from ``source`` into ``target`` with null discipline."""
    for field_name in _ADDITIVE_METRIC_FIELDS:
        src_value = cast(float | int | None, getattr(source, field_name))
        if src_value is None:
            continue
        current = cast(float | int | None, getattr(target, field_name))
        new_value = (current or 0) + src_value
        setattr(target, field_name, new_value)


def aggregate_meter_rollups(
    event_quantities: Iterable[tuple[str, Sequence[MeteredQuantity]]],
) -> list[MeterRollup]:
    """Aggregate exact meter keys while reconciling duplicate event identities."""
    seen: dict[str, str] = {}
    grouped: dict[tuple[str, str, str, str], dict[str, object]] = {}
    for event_id, quantities in event_quantities:
        require_typed_id(event_id, "evt")
        canonical = json.dumps(
            [quantity.model_dump(mode="json") for quantity in quantities],
            sort_keys=True,
            separators=(",", ":"),
        )
        existing = seen.get(event_id)
        if existing is not None:
            if existing != canonical:
                raise ValueError(
                    f"duplicate resource event identity {event_id!r} has conflicting meters"
                )
            continue
        seen[event_id] = canonical
        for quantity in quantities:
            key_tuple = quantity.key.sort_key()
            bucket = grouped.setdefault(
                key_tuple,
                {
                    "key": quantity.key,
                    "actual": None,
                    "estimated": None,
                    "unmeasured": 0,
                    "event_ids": [],
                },
            )
            event_ids = cast(list[str], bucket["event_ids"])
            event_ids.append(event_id)
            if quantity.coverage is CoverageState.MEASURED:
                bucket["actual"] = _sum_optional(
                    cast(float | None, bucket["actual"]), quantity.actual_quantity
                )
            elif quantity.coverage is CoverageState.ESTIMATED:
                bucket["estimated"] = _sum_optional(
                    cast(float | None, bucket["estimated"]),
                    quantity.estimated_quantity,
                )
            else:
                bucket["unmeasured"] = cast(int, bucket["unmeasured"]) + 1

    return [
        _meter_rollup_from_parts(
            key=cast(MeterKey, bucket["key"]),
            actual=cast(float | None, bucket["actual"]),
            estimated=cast(float | None, bucket["estimated"]),
            unmeasured=cast(int, bucket["unmeasured"]),
            source_event_ids=sorted(cast(list[str], bucket["event_ids"])),
        )
        for _, bucket in sorted(grouped.items())
    ]


def _sum_optional(left: float | None, right: float | None) -> float | None:
    if right is None:
        return left
    return (left if left is not None else 0.0) + right


def _meter_rollup_from_parts(
    *,
    key: MeterKey,
    actual: float | None,
    estimated: float | None,
    unmeasured: int,
    source_event_ids: list[str],
) -> MeterRollup:
    coverage = (
        CoverageState.MEASURED
        if actual is not None and estimated is None and unmeasured == 0
        else CoverageState.ESTIMATED
        if actual is None and estimated is not None and unmeasured == 0
        else CoverageState.UNMEASURED
    )
    return MeterRollup(
        key=key,
        coverage=coverage,
        actual_quantity=actual,
        estimated_quantity=estimated,
        unmeasured_event_count=unmeasured,
        source_event_ids=source_event_ids,
    )


def _add_meter_rollups_into(target: list[MeterRollup], additions: Sequence[MeterRollup]) -> None:
    by_key = {rollup.key.sort_key(): rollup for rollup in target}
    for addition in additions:
        key_tuple = addition.key.sort_key()
        existing = by_key.get(key_tuple)
        if existing is None:
            by_key[key_tuple] = addition.model_copy(deep=True)
            continue
        overlap = set(existing.source_event_ids) & set(addition.source_event_ids)
        if overlap:
            if existing == addition:
                continue
            raise ValueError(
                "overlapping source event identities cannot be merged from aggregate meter rows: "
                + ", ".join(sorted(overlap))
            )
        by_key[key_tuple] = _meter_rollup_from_parts(
            key=existing.key,
            actual=_sum_optional(existing.actual_quantity, addition.actual_quantity),
            estimated=_sum_optional(existing.estimated_quantity, addition.estimated_quantity),
            unmeasured=(existing.unmeasured_event_count + addition.unmeasured_event_count),
            source_event_ids=sorted([*existing.source_event_ids, *addition.source_event_ids]),
        )
    target[:] = [by_key[key] for key in sorted(by_key)]


def _reconcile_resource_event(
    raw_event: ResourceEvent,
    seen_events: dict[str, str],
) -> ResourceEvent | None:
    event = _ensure_event_id(raw_event)
    event_id = event.event_id
    if event_id is None:
        raise AssertionError("resource event identity generation failed")
    substantive_payload = event.model_dump(
        mode="json",
        exclude={"event_id", "source"},
    )
    canonical = json.dumps(substantive_payload, sort_keys=True, separators=(",", ":"))
    existing = seen_events.get(event_id)
    if existing is not None:
        if existing != canonical:
            raise ValueError(f"duplicate resource event identity {event_id!r} conflicts")
        return None
    seen_events[event_id] = canonical
    return event


def _ensure_event_id(event: ResourceEvent) -> ResourceEvent:
    if event.event_id is not None:
        return event
    portable_event = event.model_copy(
        update={"source": event.source.model_copy(update={"mtime_ns": None})}
    )
    payload = portable_event.model_dump(mode="json", exclude={"event_id"})
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).digest()
    suffix = base64.b32encode(digest[:_EVENT_ID_DIGEST_BYTES]).decode().lower().rstrip("=")
    event_id = new_typed_id("evt", unique_suffix=suffix)
    return _RESOURCE_EVENT_ADAPTER.validate_python(
        {**event.model_dump(mode="python"), "event_id": event_id}
    )


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
