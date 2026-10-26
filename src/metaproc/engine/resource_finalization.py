"""Causal, local-only finalization and recovery for run resource artifacts."""

from __future__ import annotations

import signal
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from metaproc.engine.finalization_lock import finalization_publish_lock
from metaproc.engine.finalization_outcome import (
    finalization_outcome_snapshot,
    read_finalization_outcome,
    write_finalization_outcome,
)
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


@dataclass(frozen=True)
class CausalOutcome:
    """How a run ended, as recovery reconstructs it from what the run recorded."""

    state: FinalizationState
    terminal_error_type: str | None = None


def infer_recovery_outcome(
    run_dir: Path,
    *,
    totals: ProgressCounts | None = None,
) -> FinalizationState:
    """Return only the state of :func:`infer_causal_outcome`."""
    return infer_causal_outcome(run_dir, totals=totals).state


def infer_causal_outcome(
    run_dir: Path,
    *,
    totals: ProgressCounts | None = None,
) -> CausalOutcome:
    """Preserve a causal outcome, then infer only from persisted terminal progress.

    The run's own ``.state/finalization-outcome.json`` is the authority: a launch records
    it first in every publication, and recovery never writes one, so it only ever holds
    an observed outcome. A run with no record — killed by SIGKILL, or finalized before
    the record existed — falls back to the newer of the two projections' own
    finalizations, by the time each records, since either can be an earlier launch's: a
    resumed launch can publish ``resources.json`` and be killed before its summary, and a
    hard-stop signal writes an incomplete summary beside an earlier ``resources.json``.
    """
    recorded = read_finalization_outcome(run_dir)
    if recorded is not None:
        return CausalOutcome(state=recorded.state, terminal_error_type=recorded.terminal_error_type)

    projected = _newest_projected_finalization(run_dir)
    if projected is not None:
        return CausalOutcome(
            state=projected.state, terminal_error_type=projected.terminal_error_type
        )

    process_status_path = run_dir / ".state" / "process-status.yaml"
    if process_status_path.exists():
        try:
            process_status = read_yaml_file(process_status_path)
            if isinstance(process_status, Mapping):
                state = process_status.get("state")
                if isinstance(state, str):
                    try:
                        return CausalOutcome(state=FinalizationState(state))
                    except ValueError:
                        pass
        except Exception:  # noqa: BLE001 - recovery falls back to task progress
            pass

    if totals is not None and totals.total > 0:
        if totals.completed + totals.cached >= totals.total:
            return CausalOutcome(state=FinalizationState.COMPLETED)
    return CausalOutcome(state=FinalizationState.FAILED)


def _newest_projected_finalization(run_dir: Path) -> ResourceFinalization | None:
    """Return the later of the run's two projections' finalizations, or ``None``.

    Ties go to the summary: a publication writes ``resources.json`` first and the summary
    last, so the two carry one finalization, and an incomplete summary a signal wrote
    beside an untouched ``resources.json`` is never older than it.
    """
    summarized = read_resource_usage_finalization(run_dir)
    projected = _resources_document_finalization(run_dir)
    if summarized is None:
        return projected
    if projected is None:
        return summarized
    return summarized if summarized.finalized_at >= projected.finalized_at else projected


def _resources_document_finalization(run_dir: Path) -> ResourceFinalization | None:
    """Return the finalization ``resources.json`` records, or ``None`` if none reads."""
    resources_path = run_dir / RESOURCES_RELATIVE
    if not resources_path.exists():
        return None
    try:
        document = read_resources_document(resources_path)
    except Exception:  # noqa: BLE001 - stale or malformed projections are rebuilt
        return None
    if isinstance(document, (ResourcesDocument, ResourcesDocumentV2)):
        return document.finalization
    return None


def finalize_run_resources(
    run_dir: Path,
    *,
    outcome: FinalizationState,
    legacy_run_id: str | None = None,
    trigger: Literal["terminal", "resume", "status", "recovery"] = "terminal",
    terminal_error: BaseException | None = None,
    terminal_signal: signal.Signals | None = None,
    terminal_error_type: str | None = None,
    finalized_at: datetime | None = None,
    bundle: PlanBundle | None = None,
    write_events: bool = True,
    signal_exit: bool = False,
) -> ResourceBuildResult:
    """Replay local evidence and atomically replace all resource projections.

    The caller supplies the causal terminal outcome. This function never inspects
    historical error strings to reinterpret it, and it performs no network calls.
    ``terminal_signal`` names the hard-stop signal that ended the run when no error did,
    and ``terminal_error_type`` names what ended it when only a record of it survives, as
    recovery has. ``write_events=False`` leaves ``.logs/resource-events.jsonl`` as it is,
    for a run whose live orchestrator may still append to it. ``signal_exit=True`` marks
    the publication a hard-stop signal runs, which inherits a lock this process holds
    rather than waiting for the interrupted thread that will never release it.

    The evidence is read first; the run's causal outcome and then its projections are
    published under the run's finalization lock.
    """
    result = _build_finalization(
        run_dir,
        outcome=outcome,
        legacy_run_id=legacy_run_id,
        trigger=trigger,
        terminal_error=terminal_error,
        terminal_signal=terminal_signal,
        terminal_error_type=terminal_error_type,
        finalized_at=finalized_at,
        bundle=bundle,
    )
    with finalization_publish_lock(run_dir, adopt_local_holder=signal_exit):
        _publish_finalization(result, run_dir, write_events=write_events)
    return result


def recover_run_resources(
    run_dir: Path,
    *,
    outcome: FinalizationState,
    observed_summary: bytes | None,
    observed_outcome: bytes | None = None,
    terminal_error_type: str | None = None,
    bundle: PlanBundle | None = None,
    write_events: bool = True,
) -> bool:
    """Rebuild a run's resource projections for ``metaproc status``; return whether it did.

    ``observed_summary`` and ``observed_outcome`` are the run's
    ``resource-usage-summary.md`` and ``.state/finalization-outcome.json`` as status found
    them when it decided to recover (:func:`resource_usage_summary_snapshot`,
    :func:`~metaproc.engine.finalization_outcome.finalization_outcome_snapshot`). Reading
    the evidence can take minutes on a large run, and the run's own orchestrator can
    record an outcome or finalize meanwhile: a resume that ends, a launch started after
    status found the run inactive, or a hard-stop signal, whose mark takes no lock. So the
    projections are published only if, under the finalization lock, both are still the
    ones status observed; anything else is the orchestrator's, and status's older reading
    would replace its outcome and totals for good.

    Recovery writes no outcome record of its own: the record is what a launch observed.
    """
    result = _build_finalization(
        run_dir,
        outcome=outcome,
        trigger="status",
        terminal_error_type=terminal_error_type,
        bundle=bundle,
    )
    with finalization_publish_lock(run_dir):
        if resource_usage_summary_snapshot(run_dir) != observed_summary:
            return False
        if finalization_outcome_snapshot(run_dir) != observed_outcome:
            return False
        _publish_finalization(result, run_dir, write_events=write_events)
    return True


def resource_usage_summary_snapshot(run_dir: Path) -> bytes | None:
    """Return the bytes of the run's ``resource-usage-summary.md``, or ``None`` if absent."""
    try:
        return (run_dir / RESOURCE_USAGE_SUMMARY_FILE).read_bytes()
    except FileNotFoundError:
        return None


def _build_finalization(
    run_dir: Path,
    *,
    outcome: FinalizationState,
    trigger: Literal["terminal", "resume", "status", "recovery"],
    legacy_run_id: str | None = None,
    terminal_error: BaseException | None = None,
    terminal_signal: signal.Signals | None = None,
    terminal_error_type: str | None = None,
    finalized_at: datetime | None = None,
    bundle: PlanBundle | None = None,
) -> ResourceBuildResult:
    """Replay local evidence into finalized resource projections, writing nothing."""
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
        resolved_error_type: str | None = type(terminal_error).__name__
    elif terminal_signal is not None:
        resolved_error_type = terminal_signal.name
    else:
        # What recovery carries forward from the record the ended launch left.
        resolved_error_type = terminal_error_type
    finalization = ResourceFinalization(
        state=outcome,
        trigger=trigger,
        finalized_at=finalized_at or datetime.now(UTC),
        recovered=trigger in {"status", "recovery"},
        source_event_count=len(events),
        terminal_error_type=resolved_error_type,
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
    return ResourceBuildResult(
        document=document,
        events=events,
        document_path=run_dir / RESOURCES_RELATIVE,
        events_path=ledger_path,
    )


def _publish_finalization(
    result: ResourceBuildResult, run_dir: Path, *, write_events: bool
) -> None:
    """Record the causal outcome, then replace the projections that carry it.

    The outcome goes first, so a publication interrupted part way — by the signal that a
    bounded exit cannot outrun, or by the process dying — still leaves the newest causal
    fact where every recovery path reads it. A recovering writer inferred its outcome
    rather than observing it, so it writes no record: the record holds what a launch of
    this run observed, and nothing else.
    """
    finalization = result.document.finalization
    if finalization is not None and not finalization.recovered:
        write_finalization_outcome(run_dir, finalization)
    write_resource_artifacts(result, write_events=write_events)
    write_resource_usage_summary(result.document, run_dir)


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
    recorded = read_finalization_outcome(run_dir)
    if summarized is not None and recorded is not None and not _agrees(summarized, recorded):
        # The projections are an older or a lost-race publication; the record is causal.
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


def _agrees(summarized: ResourceFinalization, recorded: ResourceFinalization) -> bool:
    """Return whether a published summary carries the run's recorded causal outcome."""
    return (
        summarized.state is recorded.state
        and summarized.terminal_error_type == recorded.terminal_error_type
    )


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
