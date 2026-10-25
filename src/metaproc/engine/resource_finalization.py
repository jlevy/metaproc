"""Causal, local-only finalization and recovery for run resource artifacts."""

from __future__ import annotations

import signal
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
    read_resource_usage_finalization,
    write_resource_usage_summary,
)
from metaproc.io import iter_artifact_paths, iter_jsonl_objects, read_yaml_file
from metaproc.logutil.resource_events import read_events
from metaproc.models.authored import ProgressCounts
from metaproc.models.plan_bundle import PlanBundle
from metaproc.models.resource_budget import (
    FinalizationState,
    ResourceFinalization,
)
from metaproc.models.resource_snapshot import ResourceRunSnapshot, ResourceRunSnapshotV1
from metaproc.models.resources import (
    Node,
    ResourceEvent,
    ResourcesDocument,
    ResourcesDocumentV2,
    SourceLog,
    read_resources_document,
)
from metaproc.paths import is_native_session_log_path, process_events_log, run_config_file

RESOURCE_EVENTS_RELATIVE = ".logs/resource-events.jsonl"
RESOURCES_RELATIVE = "resources.json"


def infer_recovery_outcome(
    run_dir: Path,
    *,
    totals: ProgressCounts | None = None,
) -> FinalizationState:
    """Preserve a causal state, then infer only from persisted terminal progress.

    The summary is read first: an incomplete one, written when a hard-stop signal ended
    the run before its rollup finished, holds the latest causal state, while the
    ``resources.json`` beside it can be an earlier launch's.
    """
    summarized = read_resource_usage_finalization(run_dir)
    if summarized is not None:
        return summarized.state

    resources_path = run_dir / RESOURCES_RELATIVE
    if resources_path.exists():
        try:
            document = read_resources_document(resources_path)
            if (
                isinstance(document, (ResourcesDocument, ResourcesDocumentV2))
                and document.finalization is not None
            ):
                return document.finalization.state
        except Exception:  # noqa: BLE001 - stale or malformed projections are rebuilt
            pass

    process_status_path = run_dir / ".state" / "process-status.yaml"
    if process_status_path.exists():
        try:
            process_status = read_yaml_file(process_status_path)
            if isinstance(process_status, Mapping):
                state = process_status.get("state")
                if isinstance(state, str):
                    try:
                        return FinalizationState(state)
                    except ValueError:
                        pass
        except Exception:  # noqa: BLE001 - recovery falls back to task progress
            pass

    if totals is not None and totals.total > 0:
        if totals.completed + totals.cached >= totals.total:
            return FinalizationState.COMPLETED
    return FinalizationState.FAILED


def finalize_run_resources(
    run_dir: Path,
    *,
    outcome: FinalizationState,
    legacy_run_id: str | None = None,
    trigger: Literal["terminal", "resume", "status", "recovery"] = "terminal",
    terminal_error: BaseException | None = None,
    terminal_signal: signal.Signals | None = None,
    finalized_at: datetime | None = None,
    bundle: PlanBundle | None = None,
    write_events: bool = True,
) -> ResourceBuildResult:
    """Replay local evidence and atomically replace all resource projections.

    The caller supplies the causal terminal outcome. This function never inspects
    historical error strings to reinterpret it, and it performs no network calls.
    ``terminal_signal`` names the hard-stop signal that ended the run when no error did.
    ``write_events=False`` leaves ``.logs/resource-events.jsonl`` as it is, for a run
    whose live orchestrator may still append to it.
    """
    snapshot = read_resource_run_snapshot(run_dir)
    config = _read_run_config(run_dir)
    resolved_run_id = _resolved_run_id(snapshot, config=config, legacy_run_id=legacy_run_id)
    if snapshot is not None:
        hierarchy = snapshot.hierarchy_node()
    elif bundle is not None:
        hierarchy = build_hierarchy_skeleton(bundle, run_id=resolved_run_id, run_dir=run_dir)
    else:
        hierarchy = Node(node_type="run", node_id=resolved_run_id, label=resolved_run_id)

    extracted = build_resource_artifacts(
        bundle=None if snapshot is not None else bundle,
        hierarchy_root=hierarchy,
        mapped_composite_step_ids=(
            snapshot.mapped_composite_step_ids
            if isinstance(snapshot, ResourceRunSnapshot)
            else None
        ),
        run_dir=run_dir,
        run_id=resolved_run_id,
        write=False,
    )
    extracted_events: list[ResourceEvent] = list(extracted.events)
    source_logs: list[SourceLog] = list(extracted.document.source_logs)

    ledger_path = run_dir / RESOURCE_EVENTS_RELATIVE
    persisted_events = read_events(ledger_path) if ledger_path.exists() else []
    events = reconcile_resource_events([*extracted_events, *persisted_events])

    if terminal_error is not None:
        terminal_error_type: str | None = type(terminal_error).__name__
    else:
        terminal_error_type = terminal_signal.name if terminal_signal is not None else None
    finalization = ResourceFinalization(
        state=outcome,
        trigger=trigger,
        finalized_at=finalized_at or datetime.now(UTC),
        recovered=trigger in {"status", "recovery"},
        source_event_count=len(events),
        terminal_error_type=terminal_error_type,
    )
    document = project_resource_document(
        # Keep item/file/tool leaves materialized while parsing raw evidence.
        # Projection clears their derived metrics before replaying the ledger.
        hierarchy_root=extracted.document.hierarchy_root,
        run_id=resolved_run_id,
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
    write_resource_artifacts(result, write_events=write_events)
    write_resource_usage_summary(document, run_dir)
    return result


def resource_run_id(run_dir: Path) -> str:
    """Return the run ID resource projections are keyed by, as finalization resolves it."""
    try:
        snapshot = read_resource_run_snapshot(run_dir)
        config = _read_run_config(run_dir)
    except Exception:  # noqa: BLE001 - an unreadable config falls back to the directory
        return run_dir.name
    return _resolved_run_id(snapshot, config=config, legacy_run_id=None)


def resource_artifacts_need_recovery(run_dir: Path) -> bool:
    """Return whether terminal resource artifacts are missing, incomplete, or locally stale."""
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
    summarized = read_resource_usage_finalization(run_dir)
    if summarized is not None and summarized.incomplete:
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
            if not is_native_session_log_path(source)
        )
    except OSError:
        return True


def resume_needs_resource_backfill(run_dir: Path) -> bool:
    """Return whether an active run is a resume whose earlier launch left no usable summary.

    That launch ended without a complete resource summary: killed by SIGKILL, or by a
    hard-stop signal before its rollup finished. A first launch that is still running has
    nothing to backfill, and a complete summary, or one that does not read, is left for
    the live orchestrator's own finalization to replace.
    """
    if (run_dir / RESOURCE_USAGE_SUMMARY_FILE).exists():
        summarized = read_resource_usage_finalization(run_dir)
        if summarized is None or not summarized.incomplete:
            return False
    return _recorded_launches(run_dir, limit=2) >= 2


def _recorded_launches(run_dir: Path, *, limit: int) -> int:
    """Count the run's orchestrator launches from its root process events, up to *limit*.

    Every ``run-process`` launch that starts the root process appends one
    ``process_start`` there; nested scopes log to their own directories.
    """
    path = process_events_log(run_dir)
    if not path.is_file():
        return 0
    launches = 0
    for event in iter_jsonl_objects(path):
        if event.get("event") == "process_start":
            launches += 1
            if launches >= limit:
                break
    return launches


def _resolved_run_id(
    snapshot: ResourceRunSnapshot | ResourceRunSnapshotV1 | None,
    *,
    config: Mapping[str, object],
    legacy_run_id: str | None,
) -> str:
    if snapshot is not None:
        return snapshot.hierarchy.node_id
    return legacy_run_id or _run_id(config=config)


def _read_run_config(run_dir: Path) -> Mapping[str, object]:
    path = run_config_file(run_dir)
    if not path.exists():
        return {}
    raw = read_yaml_file(path)
    return raw if isinstance(raw, Mapping) else {}


def _run_id(*, config: Mapping[str, object]) -> str:
    process = config.get("process")
    context = config.get("run_id")
    if isinstance(process, str) and process and isinstance(context, str) and context:
        return f"{process}/{context}"
    if isinstance(context, str) and context:
        return context
    return "legacy-run"
