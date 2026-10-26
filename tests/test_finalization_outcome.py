"""One authority on how a run ended, across both projections and every recovery path.

``resources.json`` and ``resource-usage-summary.md`` are projections: each is replaced
whole, and a publication interrupted between them leaves one of each launch. So the
causal outcome is recorded on its own, the run's own writers own it, and recovery reads
it and only ever adds one where none is.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from metaproc import paths as paths_mod
from metaproc.commands.run_process import _write_run_config
from metaproc.commands.status import _recover_resource_artifacts
from metaproc.engine import resource_finalization
from metaproc.engine.finalization_outcome import (
    finalization_outcome_path,
    read_finalization_outcome,
)
from metaproc.engine.resource_finalization import (
    finalize_run_resources,
    infer_causal_outcome,
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


def _usage(input_tokens: int) -> UsageEvent:
    return UsageEvent(
        ts=datetime(2026, 8, 3, tzinfo=UTC),
        hierarchy=HierarchyRef(run_id="old/run-1"),
        metrics=Metrics(input_tokens=input_tokens),
        source=SourceRef(kind="agent_log", path=f"local-{input_tokens}.jsonl"),
    )


def _seed(tmp_path: Path, *, launches: int = 1) -> Path:
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
    _add_usage(run_dir, 3)
    for _ in range(launches):
        with ProcessEventLogger(paths_mod.process_events_log(run_dir)) as events:
            events.process_start(process="old", run_id="old/run-1", backend="local", step_count=1)
    return run_dir


def _add_usage(run_dir: Path, input_tokens: int) -> None:
    with ResourceEventLogger(run_dir / ".logs" / "resource-events.jsonl") as logger:
        logger.write(_usage(input_tokens))


def _status(run_dir: Path, *, active: bool) -> RunStatus:
    return RunStatus(run_dir=run_dir, is_active=active, totals=ProgressCounts(total=1, completed=1))


def _summary(run_dir: Path) -> ResourceUsageSummary:
    metadata = fmf_read_frontmatter(run_dir / "resource-usage-summary.md")
    assert metadata is not None
    return ResourceUsageSummary.model_validate(metadata["resource_usage"])


def _document(run_dir: Path) -> ResourcesDocument:
    document = read_resources_document(run_dir / "resources.json")
    assert isinstance(document, ResourcesDocument)
    return document


def _publish_only_resources(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch, *, outcome: FinalizationState
) -> None:
    """Finalize as a launch interrupted between its two projections would leave the run.

    ``_publish_finalization`` writes the record, then ``resources.json``, then the
    summary; this one dies at the last step.
    """

    def no_summary(*_args: Any, **_kwargs: Any) -> Path:
        raise OSError("interrupted after resources.json was published")

    monkeypatch.setattr(resource_finalization, "write_resource_usage_summary", no_summary)
    with pytest.raises(OSError, match="interrupted"):
        finalize_run_resources(run_dir, outcome=outcome)
    monkeypatch.undo()


def test_an_older_complete_summary_does_not_override_a_newer_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resumed launch completed, published ``resources.json``, and was killed before
    its summary. The earlier launch's cancelled summary is still beside it, and must not
    be read as the run's outcome."""
    run_dir = _seed(tmp_path, launches=1)
    finalize_run_resources(run_dir, outcome=FinalizationState.CANCELLED)
    _add_usage(run_dir, 5)

    _publish_only_resources(run_dir, monkeypatch, outcome=FinalizationState.COMPLETED)

    assert _summary(run_dir).finalization.state is FinalizationState.CANCELLED
    published = _document(run_dir).finalization
    assert published is not None
    assert published.state is FinalizationState.COMPLETED
    assert infer_causal_outcome(run_dir).state is FinalizationState.COMPLETED


def test_a_run_without_a_recorded_outcome_reads_the_newer_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run finalized before the record existed still must not read the older of its
    two projections as its outcome."""
    run_dir = _seed(tmp_path, launches=1)
    finalize_run_resources(run_dir, outcome=FinalizationState.CANCELLED)
    _add_usage(run_dir, 5)
    _publish_only_resources(run_dir, monkeypatch, outcome=FinalizationState.COMPLETED)
    finalization_outcome_path(run_dir).unlink()

    assert infer_causal_outcome(run_dir).state is FinalizationState.COMPLETED


def test_a_newer_incomplete_marker_keeps_precedence_over_both_projections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run completed, published ``resources.json``, and a hard-stop signal landed
    during the rest of its finalization: the signal's mark is the newest causal fact."""
    run_dir = _seed(tmp_path, launches=1)
    _publish_only_resources(run_dir, monkeypatch, outcome=FinalizationState.COMPLETED)

    write_incomplete_resource_usage_summary(
        run_dir,
        run_id="old/run-1",
        state=FinalizationState.CANCELLED,
        terminal_error_type="SIGTERM",
    )

    assert infer_causal_outcome(run_dir) == resource_finalization.CausalOutcome(
        state=FinalizationState.CANCELLED, terminal_error_type="SIGTERM"
    )
    finalization_outcome_path(run_dir).unlink()
    assert infer_causal_outcome(run_dir).state is FinalizationState.CANCELLED


def test_recovery_keeps_the_signal_that_ended_the_run(tmp_path: Path) -> None:
    """Status rebuilds the summary a hard stop left incomplete; the replacement names
    the same signal, in the summary and in ``resources.json``."""
    run_dir = _seed(tmp_path, launches=1)
    write_incomplete_resource_usage_summary(
        run_dir,
        run_id="old/run-1",
        state=FinalizationState.CANCELLED,
        terminal_error_type="SIGQUIT",
    )

    _recover_resource_artifacts(run_dir, _status(run_dir, active=False))

    summary = _summary(run_dir).finalization
    assert (summary.state, summary.terminal_error_type, summary.incomplete) == (
        FinalizationState.CANCELLED,
        "SIGQUIT",
        False,
    )
    document = _document(run_dir).finalization
    assert document is not None
    assert (document.state, document.terminal_error_type) == (
        FinalizationState.CANCELLED,
        "SIGQUIT",
    )
    assert not resource_artifacts_need_recovery(run_dir)


def test_recovery_does_not_replace_an_observed_outcome(tmp_path: Path) -> None:
    """Recovery infers an outcome from evidence; a launch observed one. The launch's
    record stands, so repeated recovery cannot drift away from it."""
    run_dir = _seed(tmp_path, launches=1)
    write_incomplete_resource_usage_summary(
        run_dir,
        run_id="old/run-1",
        state=FinalizationState.CANCELLED,
        terminal_error_type="SIGTERM",
    )
    recorded = finalization_outcome_path(run_dir).read_bytes()

    _recover_resource_artifacts(run_dir, _status(run_dir, active=False))

    assert finalization_outcome_path(run_dir).read_bytes() == recorded


def test_recovery_records_no_outcome_of_its_own(tmp_path: Path) -> None:
    """A run killed by SIGKILL observed nothing, so there is nothing to record. Recovery
    writes no record: an inference that outlived the evidence it was drawn from would be
    worse than no record, since the record takes precedence over both projections."""
    run_dir = _seed(tmp_path, launches=1)

    _recover_resource_artifacts(run_dir, _status(run_dir, active=False))

    assert not finalization_outcome_path(run_dir).exists()
    assert _summary(run_dir).finalization.recovered is True
    # The outcome still reads back from the projections recovery published.
    assert infer_causal_outcome(run_dir).state is FinalizationState.COMPLETED

    # Evidence the first recovery could not see still changes the answer.
    (run_dir / ".state" / "process-status.yaml").write_text("state: failed\n", encoding="utf-8")
    (run_dir / "resource-usage-summary.md").unlink()
    (run_dir / "resources.json").unlink()

    assert infer_causal_outcome(run_dir).state is FinalizationState.FAILED


def test_a_status_publication_past_its_check_cannot_erase_the_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Status compared an absent summary, took the lock, and a hard-stop signal's mark —
    which takes no lock, since the thread it interrupts may hold one — then recorded the
    run's cancellation. Status's own projection may land, but the cancellation is not
    lost: the record stands, the artifacts beside it read as needing recovery, and the
    next recovery restores them."""
    run_dir = _seed(tmp_path, launches=1)
    real_publish = resource_finalization._publish_finalization
    marked: list[bool] = []

    def signal_marks_then_publish(*args: Any, **kwargs: Any) -> None:
        if not marked:
            marked.append(True)
            write_incomplete_resource_usage_summary(
                run_dir,
                run_id="old/run-1",
                state=FinalizationState.CANCELLED,
                terminal_error_type="SIGTERM",
            )
        real_publish(*args, **kwargs)

    monkeypatch.setattr(resource_finalization, "_publish_finalization", signal_marks_then_publish)

    _recover_resource_artifacts(run_dir, _status(run_dir, active=False))
    monkeypatch.undo()

    assert marked
    recorded = read_finalization_outcome(run_dir)
    assert recorded is not None
    assert (recorded.state, recorded.terminal_error_type) == (
        FinalizationState.CANCELLED,
        "SIGTERM",
    )
    assert resource_artifacts_need_recovery(run_dir)

    _recover_resource_artifacts(run_dir, _status(run_dir, active=False))

    summary = _summary(run_dir).finalization
    assert (summary.state, summary.terminal_error_type) == (
        FinalizationState.CANCELLED,
        "SIGTERM",
    )
    assert not resource_artifacts_need_recovery(run_dir)
