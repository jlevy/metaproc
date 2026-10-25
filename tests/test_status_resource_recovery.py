"""Status-triggered resource recovery is local, and a backstop for every exit.

An inactive run gets missing, stale or incomplete summaries rebuilt. An active run is
left alone unless it is a resume whose earlier orchestrator ended without a complete
resource summary; then status writes that summary without touching the live ledger.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from metaproc import paths as paths_mod
from metaproc.commands.run_process import _write_run_config
from metaproc.commands.status import _recover_resource_artifacts
from metaproc.engine.operations_summary import read_operations_summary
from metaproc.engine.resource_finalization import (
    finalize_run_resources,
    resource_artifacts_need_recovery,
)
from metaproc.engine.resource_summary import write_incomplete_resource_usage_summary
from metaproc.engine.run_status import RunStatus
from metaproc.io import fmf_read_frontmatter
from metaproc.logutil.resource_events import ResourceEventLogger
from metaproc.models.authored import ProgressCounts
from metaproc.models.resource_budget import FinalizationState
from metaproc.models.resource_summary import ResourceUsageSummary
from metaproc.models.resources import (
    HierarchyRef,
    Metrics,
    ResourcesDocument,
    SourceRef,
    UsageEvent,
    read_resources_document,
)
from metaproc.runpool.process_events import ProcessEventLogger


def _seed(tmp_path: Path, *, launches: int = 1) -> Path:
    """A run with one usage event and ``launches`` recorded orchestrator launches."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_run_config(
        run_dir,
        process_name="old",
        process_path=tmp_path / "removed.process.md",
        run_id="run-1",
        variables={},
        backend="local",
        variant=None,
    )
    event = UsageEvent(
        ts=datetime(2026, 8, 3, tzinfo=UTC),
        hierarchy=HierarchyRef(run_id="old/run-1"),
        metrics=Metrics(input_tokens=3),
        source=SourceRef(kind="agent_log", path="local.jsonl"),
    )
    with ResourceEventLogger(run_dir / ".logs" / "resource-events.jsonl") as logger:
        logger.write(event)
    for _ in range(launches):
        _record_launch(run_dir)
    return run_dir


def _record_launch(run_dir: Path) -> None:
    with ProcessEventLogger(paths_mod.process_events_log(run_dir)) as events:
        events.process_start(process="old", run_id="old/run-1", backend="local", step_count=1)


def _status(run_dir: Path, *, active: bool) -> RunStatus:
    return RunStatus(
        run_dir=run_dir,
        is_active=active,
        totals=ProgressCounts(total=1, completed=1),
    )


def _summary(run_dir: Path) -> ResourceUsageSummary:
    metadata = fmf_read_frontmatter(run_dir / "resource-usage-summary.md")
    assert metadata is not None
    return ResourceUsageSummary.model_validate(metadata["resource_usage"])


def test_inactive_status_recovers_missing_artifacts_from_local_ledger(tmp_path: Path) -> None:
    run_dir = _seed(tmp_path)

    _recover_resource_artifacts(run_dir, _status(run_dir, active=False))

    document = read_resources_document(run_dir / "resources.json")
    assert isinstance(document, ResourcesDocument)
    assert document.hierarchy_root.total_metrics.input_tokens == 3
    assert document.finalization is not None
    assert document.finalization.state is FinalizationState.COMPLETED
    assert document.finalization.recovered is True


def test_active_first_launch_is_left_alone(tmp_path: Path) -> None:
    """A run on its first launch has not ended, so it has no summary to write yet."""
    run_dir = _seed(tmp_path, launches=1)

    _recover_resource_artifacts(run_dir, _status(run_dir, active=True))

    assert not (run_dir / "resources.json").exists()
    assert not (run_dir / "resource-usage-summary.md").exists()
    assert not (run_dir / "operations-summary.md").exists()


def test_active_resume_backfills_the_summary_an_earlier_launch_never_wrote(
    tmp_path: Path,
) -> None:
    """The earlier orchestrator was killed before finalizing and a resume is running:
    status writes the summaries from local evidence and leaves the live ledger as is."""
    run_dir = _seed(tmp_path, launches=2)
    ledger = run_dir / ".logs" / "resource-events.jsonl"
    ledger_before = ledger.read_bytes()

    _recover_resource_artifacts(run_dir, _status(run_dir, active=True))

    summary = _summary(run_dir)
    assert summary.totals.input_tokens == 3
    assert (summary.finalization.trigger, summary.finalization.recovered) == ("status", True)
    assert ledger.read_bytes() == ledger_before
    operations = read_operations_summary(run_dir).summary
    assert operations is not None and operations.trigger == "status"


def test_active_resume_keeps_a_cooperatively_finalized_summary(tmp_path: Path) -> None:
    run_dir = _seed(tmp_path, launches=1)
    finalize_run_resources(run_dir, outcome=FinalizationState.FAILED)
    _record_launch(run_dir)
    written = (run_dir / "resource-usage-summary.md").read_bytes()

    _recover_resource_artifacts(run_dir, _status(run_dir, active=True))

    assert (run_dir / "resource-usage-summary.md").read_bytes() == written
    assert not (run_dir / "operations-summary.md").exists()


def test_active_resume_rebuilds_an_incomplete_summary(tmp_path: Path) -> None:
    """A hard-stop signal ended the earlier launch before its rollup finished."""
    run_dir = _seed(tmp_path, launches=2)
    write_incomplete_resource_usage_summary(
        run_dir,
        run_id="old/run-1",
        state=FinalizationState.CANCELLED,
        terminal_error_type="SIGTERM",
    )

    _recover_resource_artifacts(run_dir, _status(run_dir, active=True))

    summary = _summary(run_dir)
    assert summary.finalization.incomplete is False
    assert summary.finalization.state is FinalizationState.CANCELLED
    assert summary.totals.input_tokens == 3


def test_inactive_status_rebuilds_an_incomplete_summary_and_keeps_its_state(
    tmp_path: Path,
) -> None:
    run_dir = _seed(tmp_path)
    _recover_resource_artifacts(run_dir, _status(run_dir, active=False))
    assert not resource_artifacts_need_recovery(run_dir)
    write_incomplete_resource_usage_summary(
        run_dir,
        run_id="old/run-1",
        state=FinalizationState.CANCELLED,
        terminal_error_type="SIGTERM",
    )
    assert resource_artifacts_need_recovery(run_dir)

    _recover_resource_artifacts(run_dir, _status(run_dir, active=False))

    summary = _summary(run_dir)
    assert summary.finalization.incomplete is False
    assert summary.finalization.state is FinalizationState.CANCELLED
    assert summary.totals.input_tokens == 3
    assert not resource_artifacts_need_recovery(run_dir)


def test_inactive_status_restores_missing_summary_schema(tmp_path: Path) -> None:
    run_dir = _seed(tmp_path)
    _recover_resource_artifacts(run_dir, _status(run_dir, active=False))
    schema = run_dir / ".state" / "schemas" / "resource-usage-summary.v1.schema.yaml"
    schema.unlink()

    _recover_resource_artifacts(run_dir, _status(run_dir, active=False))

    assert schema.is_file()


def test_inactive_status_rebuilds_a_summary_older_than_local_evidence(tmp_path: Path) -> None:
    run_dir = _seed(tmp_path)
    status = _status(run_dir, active=False)
    _recover_resource_artifacts(run_dir, status)
    summary = run_dir / "resource-usage-summary.md"
    summary.write_text("stale summary\n")
    os.utime(summary, ns=(1, 1))

    _recover_resource_artifacts(run_dir, status)

    assert summary.read_text().startswith("---\n")
    assert "# Resource usage summary" in summary.read_text()


def test_inactive_status_writes_a_missing_operations_summary_once(tmp_path: Path) -> None:
    """A run killed by SIGKILL never reaches finalization; status recovery writes its summary."""
    run_dir = _seed(tmp_path)
    status = _status(run_dir, active=False)

    _recover_resource_artifacts(run_dir, status)

    summary = read_operations_summary(run_dir).summary
    assert summary is not None and summary.run is not None
    assert (summary.trigger, summary.run.state, summary.run.state_source) == (
        "status",
        "completed",
        "resource_summary",
    )
    written = (run_dir / "operations-summary.md").read_bytes()
    resource_summary = run_dir / "resource-usage-summary.md"
    resource_summary.write_text("stale summary\n")
    os.utime(resource_summary, ns=(1, 1))

    _recover_resource_artifacts(run_dir, status)

    assert resource_summary.read_text().startswith("---\n")
    assert (run_dir / "operations-summary.md").read_bytes() == written


def test_inactive_status_writes_a_missing_operations_summary_beside_fresh_resources(
    tmp_path: Path,
) -> None:
    """Fresh resource artifacts and no summary is reachable: a SIGKILL between the two
    finalizers, a fold that raised at finalization, or resources an earlier status
    already recovered. Recovering nothing must still leave the run a summary."""
    run_dir = _seed(tmp_path)
    status = _status(run_dir, active=False)
    _recover_resource_artifacts(run_dir, status)
    (run_dir / "operations-summary.md").unlink()
    assert not resource_artifacts_need_recovery(run_dir)

    _recover_resource_artifacts(run_dir, status)

    summary = read_operations_summary(run_dir).summary
    assert summary is not None and summary.run is not None
    assert (summary.trigger, summary.run.state_source) == ("status", "resource_summary")
