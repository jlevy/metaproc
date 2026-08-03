"""Terminal resource finalization and local recovery contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from softschema import Contract, SchemaStatus, compile_model, validate_artifact

from metaproc.commands.run_process import _write_run_config
from metaproc.engine.resource_finalization import finalize_run_resources
from metaproc.io import fmf_read_frontmatter
from metaproc.logutil.resource_events import ResourceEventLogger
from metaproc.models.resource_budget import (
    BudgetStatus,
    FinalizationState,
    ResourceBudgetSpec,
)
from metaproc.models.resource_snapshot import ResourceRunSnapshot, ResourceTopologyNode
from metaproc.models.resource_summary import (
    RESOURCE_USAGE_SUMMARY_CONTRACT,
    ResourceUsageSummary,
)
from metaproc.models.resources import HierarchyRef, Metrics, SourceRef, UsageEvent
from metaproc.plugins.discovery import get_plugin_registry
from metaproc.viz_loader import load_plan_bundle

_LEGACY_PROCESS = """\
---
process:
  name: example
  steps:
    - id: analyze
      mode: code
      command: "true"
---

Legacy process.
"""


def _event() -> UsageEvent:
    return UsageEvent(
        ts=datetime(2026, 8, 3, tzinfo=UTC),
        hierarchy=HierarchyRef(run_id="example/run-1", step_node_id="analyze"),
        metrics=Metrics(
            input_tokens=7,
            output_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
        ),
        source=SourceRef(kind="agent_log", path="missing-session.jsonl"),
    )


def _snapshot() -> ResourceRunSnapshot:
    return ResourceRunSnapshot(
        hierarchy=ResourceTopologyNode(
            node_type="run",
            node_id="example/run-1",
            label="example/run-1",
            children=[
                ResourceTopologyNode(
                    node_type="process",
                    node_id="process:root",
                    label="root",
                    parent_id="example/run-1",
                    children=[
                        ResourceTopologyNode(
                            node_type="step",
                            node_id="analyze",
                            label="analyze",
                            parent_id="process:root",
                        )
                    ],
                )
            ],
        ),
        budgets=[
            ResourceBudgetSpec.model_validate(
                {
                    "budget_id": "bud-input-limit",
                    "scope": {"kind": "step", "key": "analyze"},
                    "metric": "input_tokens",
                    "threshold": 5,
                }
            )
        ],
    )


def _seed_run(tmp_path: Path, *, snapshot: ResourceRunSnapshot | None) -> Path:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    _write_run_config(
        run_dir,
        process_name="example",
        process_path=tmp_path / "removed.process.md",
        run_id="run-1",
        variables={"RUN_ID": "run-1"},
        backend="local",
        variant=None,
        resource_snapshot=snapshot,
    )
    with ResourceEventLogger(run_dir / ".logs" / "resource-events.jsonl") as logger:
        logger.write(_event())
    return run_dir


def test_finalization_replays_ledger_snapshot_and_budget(tmp_path: Path) -> None:
    run_dir = _seed_run(tmp_path, snapshot=_snapshot())

    result = finalize_run_resources(
        run_dir,
        outcome=FinalizationState.FAILED,
        trigger="terminal",
        terminal_error=RuntimeError("process failed"),
        finalized_at=datetime(2026, 8, 3, 1, tzinfo=UTC),
    )

    assert result.document.hierarchy_root.total_metrics.input_tokens == 7
    assert result.document.finalization is not None
    assert result.document.finalization.state is FinalizationState.FAILED
    assert result.document.finalization.terminal_error_type == "RuntimeError"
    assert result.document.budget_evaluations[0].status is BudgetStatus.EXCEEDED
    assert (run_dir / "resources.json").is_file()
    assert (run_dir / "resource-usage-summary.md").is_file()


def test_generated_summary_is_self_describing_and_softschema_valid(tmp_path: Path) -> None:
    run_dir = _seed_run(tmp_path, snapshot=_snapshot())
    finalize_run_resources(run_dir, outcome=FinalizationState.COMPLETED)

    summary_path = run_dir / "resource-usage-summary.md"
    metadata = fmf_read_frontmatter(summary_path)
    assert metadata is not None
    assert metadata["softschema"] == {
        "contract": RESOURCE_USAGE_SUMMARY_CONTRACT,
        "schema": ".state/schemas/resource-usage-summary.v1.schema.yaml",
        "envelope": "resource_usage",
        "status": "enforced",
    }
    validation = validate_artifact(
        summary_path,
        contract=Contract(
            id=RESOURCE_USAGE_SUMMARY_CONTRACT,
            model=ResourceUsageSummary,
            envelope_key="resource_usage",
            status=SchemaStatus.enforced,
            schema_path=run_dir / ".state" / "schemas" / "resource-usage-summary.v1.schema.yaml",
        ),
    )
    assert validation.ok, (validation.structural.errors, validation.semantic.errors)


def test_committed_resource_summary_schema_has_no_compilation_drift() -> None:
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "metaproc"
        / "data"
        / "schemas"
        / "resource-usage-summary.v1.schema.yaml"
    )

    result = compile_model(
        ResourceUsageSummary,
        schema_path,
        contract_id=RESOURCE_USAGE_SUMMARY_CONTRACT,
        check_only=True,
    )

    assert result.drift is False, result.drift_diff


def test_resource_summary_contract_is_registered() -> None:
    contract = get_plugin_registry().softschemas.resolve(RESOURCE_USAGE_SUMMARY_CONTRACT)

    assert contract is not None
    assert contract.model is ResourceUsageSummary
    assert contract.envelope_key == "resource_usage"


def test_repeated_finalization_does_not_double_count_ledger(tmp_path: Path) -> None:
    run_dir = _seed_run(tmp_path, snapshot=_snapshot())

    finalize_run_resources(run_dir, outcome=FinalizationState.COMPLETED)
    again = finalize_run_resources(run_dir, outcome=FinalizationState.COMPLETED, trigger="recovery")

    assert again.document.hierarchy_root.total_metrics.input_tokens == 7
    assert again.document.finalization is not None
    assert again.document.finalization.source_event_count == 1
    assert again.document.finalization.recovered is True


def test_legacy_run_uses_root_projection_without_current_budgets(tmp_path: Path) -> None:
    run_dir = _seed_run(tmp_path, snapshot=None)

    result = finalize_run_resources(run_dir, outcome=FinalizationState.FAILED, trigger="status")

    assert result.document.hierarchy_root.self_metrics.input_tokens == 7
    assert result.document.budget_evaluations == []


def test_legacy_run_with_bundle_retains_process_hierarchy(tmp_path: Path) -> None:
    run_dir = _seed_run(tmp_path, snapshot=None)
    process_path = tmp_path / "legacy.process.md"
    process_path.write_text(_LEGACY_PROCESS)

    result = finalize_run_resources(
        run_dir,
        outcome=FinalizationState.FAILED,
        trigger="status",
        bundle=load_plan_bundle(process_path),
    )

    process = result.document.hierarchy_root.children[0]
    analyze = process.children[0]
    assert analyze.total_metrics.input_tokens == 7
    assert result.document.budget_evaluations == []


def test_recovery_parses_raw_task_log_when_spec_and_ledger_are_missing(tmp_path: Path) -> None:
    run_dir = _seed_run(tmp_path, snapshot=_snapshot())
    (run_dir / ".logs" / "resource-events.jsonl").unlink()
    log_path = run_dir / ".logs" / "tasks" / "analyze" / "session.jsonl"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "system",
                        "subtype": "init",
                        "model": "claude-opus-4-6",
                        "timestamp": "2026-08-03T00:00:00Z",
                    }
                ),
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "usage": {"input_tokens": 11, "output_tokens": 2},
                        "timestamp": "2026-08-03T00:00:01Z",
                    }
                ),
            ]
        )
        + "\n"
    )

    result = finalize_run_resources(
        run_dir,
        outcome=FinalizationState.FAILED,
        trigger="recovery",
    )

    process = result.document.hierarchy_root.children[0]
    analyze = process.children[0]
    assert analyze.total_metrics.input_tokens == 11
    assert analyze.log_summary.source_log_count == 1
    assert result.document.finalization is not None
    assert result.document.finalization.source_event_count > 0
