"""Status-triggered resource recovery is local and inactive-only."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from metaproc.commands.run_process import _write_run_config
from metaproc.commands.status import _recover_resource_artifacts
from metaproc.engine.run_status import RunStatus
from metaproc.logutil.resource_events import ResourceEventLogger
from metaproc.models.authored import ProgressCounts
from metaproc.models.resource_budget import FinalizationState
from metaproc.models.resources import (
    HierarchyRef,
    Metrics,
    ResourcesDocument,
    SourceRef,
    UsageEvent,
    read_resources_document,
)


def _seed(tmp_path: Path) -> Path:
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
    return run_dir


def _status(run_dir: Path, *, active: bool) -> RunStatus:
    return RunStatus(
        run_dir=run_dir,
        is_active=active,
        totals=ProgressCounts(total=1, completed=1),
    )


def test_inactive_status_recovers_missing_artifacts_from_local_ledger(tmp_path: Path) -> None:
    run_dir = _seed(tmp_path)

    _recover_resource_artifacts(run_dir, _status(run_dir, active=False))

    document = read_resources_document(run_dir / "resources.json")
    assert isinstance(document, ResourcesDocument)
    assert document.hierarchy_root.total_metrics.input_tokens == 3
    assert document.finalization is not None
    assert document.finalization.state is FinalizationState.COMPLETED
    assert document.finalization.recovered is True


def test_active_status_never_recovers_or_rewrites_resources(tmp_path: Path) -> None:
    run_dir = _seed(tmp_path)

    _recover_resource_artifacts(run_dir, _status(run_dir, active=True))

    assert not (run_dir / "resources.json").exists()
    assert not (run_dir / "resource-usage-summary.md").exists()


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
