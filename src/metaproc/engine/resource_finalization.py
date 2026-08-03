"""Causal, local-only finalization and recovery for run resource artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from metaproc.engine.resource_hierarchy import build_hierarchy_skeleton
from metaproc.engine.resource_reconciliation import reconcile_resource_events
from metaproc.engine.resource_rollup import (
    ResourceBuildResult,
    build_resource_artifacts,
    project_resource_document,
    write_resource_artifacts,
)
from metaproc.engine.resource_snapshot import read_resource_run_snapshot
from metaproc.engine.resource_summary import (
    RESOURCE_USAGE_SCHEMA_RELATIVE,
    RESOURCE_USAGE_SUMMARY_FILE,
    write_resource_usage_summary,
)
from metaproc.io import iter_artifact_paths, read_yaml_file
from metaproc.logutil.resource_events import read_events
from metaproc.models.plan_bundle import PlanBundle
from metaproc.models.resource_budget import (
    FinalizationState,
    ResourceFinalization,
)
from metaproc.models.resources import Node, ResourceEvent, SourceLog
from metaproc.paths import run_config_file

RESOURCE_EVENTS_RELATIVE = ".logs/resource-events.jsonl"
RESOURCES_RELATIVE = "resources.json"


def finalize_run_resources(
    run_dir: Path,
    *,
    outcome: FinalizationState,
    trigger: Literal["terminal", "resume", "status", "recovery"] = "terminal",
    terminal_error: BaseException | None = None,
    finalized_at: datetime | None = None,
    bundle: PlanBundle | None = None,
) -> ResourceBuildResult:
    """Replay local evidence and atomically replace all resource projections.

    The caller supplies the causal terminal outcome. This function never inspects
    historical error strings to reinterpret it, and it performs no network calls.
    """
    snapshot = read_resource_run_snapshot(run_dir)
    config = _read_run_config(run_dir)
    run_id = _run_id(
        snapshot_root_id=snapshot.hierarchy.node_id if snapshot else None, config=config
    )
    if snapshot is not None:
        hierarchy = snapshot.hierarchy_node()
    elif bundle is not None:
        hierarchy = build_hierarchy_skeleton(bundle, run_id=run_id, run_dir=run_dir)
    else:
        hierarchy = Node(node_type="run", node_id=run_id, label=run_id)

    extracted = build_resource_artifacts(
        bundle=None if snapshot is not None else bundle,
        hierarchy_root=hierarchy,
        run_dir=run_dir,
        run_id=run_id,
        write=False,
    )
    extracted_events: list[ResourceEvent] = list(extracted.events)
    source_logs: list[SourceLog] = list(extracted.document.source_logs)

    ledger_path = run_dir / RESOURCE_EVENTS_RELATIVE
    persisted_events = read_events(ledger_path) if ledger_path.exists() else []
    events = reconcile_resource_events([*extracted_events, *persisted_events])

    finalization = ResourceFinalization(
        state=outcome,
        trigger=trigger,
        finalized_at=finalized_at or datetime.now(UTC),
        recovered=trigger in {"status", "recovery"},
        source_event_count=len(events),
        terminal_error_type=type(terminal_error).__name__ if terminal_error is not None else None,
    )
    document = project_resource_document(
        # Keep item/file/tool leaves materialized while parsing raw evidence.
        # Projection clears their derived metrics before replaying the ledger.
        hierarchy_root=extracted.document.hierarchy_root,
        run_id=run_id,
        events=events,
        source_events_path=RESOURCE_EVENTS_RELATIVE,
        source_logs=source_logs,
        budgets=snapshot.budgets if snapshot is not None else (),
        finalization=finalization,
        summary_path=RESOURCE_USAGE_SUMMARY_FILE,
    )
    result = ResourceBuildResult(
        document=document,
        events=events,
        document_path=run_dir / RESOURCES_RELATIVE,
        events_path=ledger_path,
    )
    write_resource_artifacts(result)
    write_resource_usage_summary(document, run_dir)
    return result


def resource_artifacts_need_recovery(run_dir: Path) -> bool:
    """Return whether terminal resource artifacts are missing or locally stale."""
    resources_path = run_dir / RESOURCES_RELATIVE
    ledger_path = run_dir / RESOURCE_EVENTS_RELATIVE
    summary_path = run_dir / RESOURCE_USAGE_SUMMARY_FILE
    schema_path = run_dir / RESOURCE_USAGE_SCHEMA_RELATIVE
    if (
        not resources_path.exists()
        or not ledger_path.exists()
        or not summary_path.exists()
        or not schema_path.exists()
    ):
        return True
    try:
        projection_mtime = min(
            resources_path.stat().st_mtime_ns,
            summary_path.stat().st_mtime_ns,
            schema_path.stat().st_mtime_ns,
        )
        return any(
            source.stat().st_mtime_ns > projection_mtime
            for source in iter_artifact_paths(run_dir, "**/*.jsonl")
        )
    except OSError:
        return True


def _read_run_config(run_dir: Path) -> Mapping[str, object]:
    path = run_config_file(run_dir)
    if not path.exists():
        return {}
    raw = read_yaml_file(path)
    return raw if isinstance(raw, Mapping) else {}


def _run_id(*, snapshot_root_id: str | None, config: Mapping[str, object]) -> str:
    if snapshot_root_id:
        return snapshot_root_id
    process = config.get("process")
    context = config.get("run_id")
    if isinstance(process, str) and process and isinstance(context, str) and context:
        return f"{process}/{context}"
    if isinstance(context, str) and context:
        return context
    return "legacy-run"
