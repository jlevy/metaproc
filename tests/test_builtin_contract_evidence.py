"""Built-in contracts keep native model verdicts and disclose unrun schema checks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from softschema import SchemaStatus, StructuralResult, validate_artifact

from metaproc.commands.softschema import _artifact_result_payload
from metaproc.io import fmf_write, to_yaml_string
from metaproc.io.frontmatter import ProgressSpec
from metaproc.models.authored import ProcessSpec
from metaproc.models.plan import Plan, RunPlanSnapshot, RunPlanStep
from metaproc.models.qa import QaReport, QaSummary
from metaproc.models.resource_budget import FinalizationState, ResourceFinalization
from metaproc.models.resource_summary import ResourceUsageSummary
from metaproc.models.resources import Metrics, Node, ResourcesDocument
from metaproc.models.usage import UsageReport
from metaproc.plugins.registry import PluginRegistryImpl
from metaproc.structure_report import StructureReport, StructureReportSummary


@dataclass(frozen=True)
class BuiltinArtifact:
    contract_id: str
    record: BaseModel
    envelope: str | None
    invalid_field: str
    invalid_value: object
    error_location: tuple[str, ...]
    pure_yaml: bool = False


_STAMP = datetime(2026, 1, 1, tzinfo=UTC)
_STEP = RunPlanStep(
    step_id="scan", mode="code", task_shape="scalar", item_keys=[], fingerprint="a" * 16
)
_CASES = [
    BuiltinArtifact(
        "metaproc:ResourcesDocument/0.1",
        ResourcesDocument(
            run_id="example/run-1",
            generated_at=_STAMP,
            source_events_path=".logs/resource-events.jsonl",
            hierarchy_root=Node(node_type="run", node_id="run-1", label="Example"),
        ),
        None,
        "run_id",
        [],
        ("run_id",),
        pure_yaml=True,
    ),
    BuiltinArtifact(
        "metaproc:RunPlanSnapshot/0.1",
        RunPlanSnapshot(run_id="example/run-1", steps=[_STEP]),
        "run_plan",
        "steps",
        [_STEP.model_dump(mode="json"), _STEP.model_dump(mode="json")],
        (),  # A cross-field invariant, beyond checking individual field types.
        pure_yaml=True,
    ),
    BuiltinArtifact(
        "metaproc:ProcessSpec/0.1",
        ProcessSpec(name="example"),
        "process",
        "name",
        None,
        ("name",),
    ),
    BuiltinArtifact(
        "metaproc:ProgressSpec/0.1",
        ProgressSpec(process="example", run_id="run-1", items=[{"id": "sample"}]),
        "progress",
        "items",
        "not a roster",
        ("items",),
    ),
    *[
        BuiltinArtifact(
            f"metaproc:Plan/{version}",
            Plan(schema=f"metaproc:Plan/{version}", process="example"),
            "plan",
            "steps",
            "not a step list",
            ("steps",),
        )
        for version in ("0.4", "0.5", "0.6")
    ],
    *[
        BuiltinArtifact(
            f"metaproc:QaReport/{version}",
            QaReport(
                schema=f"metaproc:QaReport/{version}",
                process="example",
                item_id="sample",
                run_id="run-1",
                variant="default",
                timestamp=_STAMP.isoformat(),
            ),
            "qa",
            "item_id",
            None,
            ("item_id",),
        )
        for version in ("0.1", "0.2")
    ],
    BuiltinArtifact(
        "metaproc:QaSummary/0.1",
        QaSummary(process="example", run_id="run-1", timestamp=_STAMP.isoformat()),
        "qa_summary",
        "totals",
        "not summary rows",
        ("totals",),
    ),
    BuiltinArtifact(
        "metaproc:UsageReport/0.2",
        UsageReport(run_id="run-1", phase="scan", generated=_STAMP.isoformat()),
        "usage",
        "totals",
        {"output_tokens": "not a count"},
        ("totals", "output_tokens"),
    ),
    BuiltinArtifact(
        "metaproc.resources:ResourceUsageSummary/v1",
        ResourceUsageSummary(
            run_id="run-1",
            totals=Metrics(),
            finalization=ResourceFinalization(
                state=FinalizationState.COMPLETED, trigger="terminal", finalized_at=_STAMP
            ),
        ),
        "resource_usage",
        "totals",
        [],
        ("totals",),
    ),
    BuiltinArtifact(
        "metaproc:StructureReport/v1",
        StructureReport(
            generated_at=_STAMP.isoformat(),
            process_path="example.process.md",
            summary=StructureReportSummary(),
        ),
        "structure_report",
        "summary",
        {"artifacts": "not a count"},
        ("summary", "artifacts"),
    ),
]


def _write_artifact(tmp_path: Path, case: BuiltinArtifact, *, rejected: bool) -> Path:
    payload = case.record.model_dump(mode="json", by_alias=True)
    if rejected:
        payload[case.invalid_field] = case.invalid_value
    metadata: dict[str, Any] = {case.envelope: payload} if case.envelope else payload
    if case.pure_yaml:
        path = tmp_path / "artifact.yaml"
        path.write_text(to_yaml_string(metadata), encoding="utf-8")
    else:
        path = tmp_path / "artifact.md"
        fmf_write(path, "# Example\n", metadata)
    return path


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.contract_id)
@pytest.mark.parametrize("rejected", [False, True], ids=["accepted", "rejected"])
def test_builtin_native_artifact_verdict(
    tmp_path: Path, case: BuiltinArtifact, rejected: bool
) -> None:
    """Registry dispatch must reach each real model, including its nested invariants."""
    path = _write_artifact(tmp_path, case, rejected=rejected)
    result = validate_artifact(
        path, contract_id=case.contract_id, registry=PluginRegistryImpl().softschemas
    )
    assert result.status is SchemaStatus.enforced
    assert result.ok is not rejected
    assert result.outcome == ("invalid" if rejected else "valid")
    assert result.semantic.ok is not rejected
    if rejected:
        assert any(tuple(error["loc"]) == case.error_location for error in result.semantic.errors)
        if case.contract_id == "metaproc:RunPlanSnapshot/0.1":
            assert "unique step IDs" in result.semantic.errors[0]["msg"]
    else:
        assert not result.semantic.errors


@pytest.mark.skipif(
    "execution" in StructuralResult.__dataclass_fields__,
    reason="Describes the pinned SoftSchema 0.8 report; replace when the pin moves (mp-3pu3)",
)
@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.contract_id)
@pytest.mark.parametrize("rejected", [False, True], ids=["accepted", "rejected"])
def test_builtin_model_report_matches_the_pinned_softschema_release(
    tmp_path: Path, case: BuiltinArtifact, rejected: bool
) -> None:
    """The operator reference's built-in contract description matches the pinned library."""
    path = _write_artifact(tmp_path, case, rejected=rejected)
    result = validate_artifact(
        path, contract_id=case.contract_id, registry=PluginRegistryImpl().softschemas
    )
    report: dict[str, Any] = _artifact_result_payload(result)
    assert report["structural"] == {
        "ok": True,
        "errors": [],
        "engine": "json_schema",
        "skipped_reason": "inferred_via_model",
    }
    assert report["semantic"]["ok"] is not rejected
    assert report["semantic"]["skipped_reason"] is None
    assert report["warnings"] == []


@pytest.mark.skipif(
    "execution" not in StructuralResult.__dataclass_fields__,
    reason=(
        "SoftSchema 0.9 execution evidence is unavailable in the pinned 0.8 dependency; "
        "unskip when the pin moves (mp-3pu3)"
    ),
)
@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.contract_id)
@pytest.mark.parametrize("rejected", [False, True], ids=["accepted", "rejected"])
def test_builtin_model_evidence_survives_report_serialization(
    tmp_path: Path, case: BuiltinArtifact, rejected: bool
) -> None:
    """A real model verdict keeps the advisory without inventing a structural check."""
    path = _write_artifact(tmp_path, case, rejected=rejected)
    result = validate_artifact(
        path, contract_id=case.contract_id, registry=PluginRegistryImpl().softschemas
    )
    report: dict[str, Any] = _artifact_result_payload(result)
    assert report["status"] == "enforced"
    assert report["outcome"] == ("invalid" if rejected else "valid")
    assert report["structural"] == {
        "ok": True,
        "execution": "not_run",
        "errors": [],
        "engine": "json_schema",
        "skipped_reason": "inferred_via_model",
    }
    assert asdict(result.semantic)["execution"] == "completed"
    assert report["semantic"] == asdict(result.semantic)
    assert [warning["code"] for warning in report["warnings"]] == [
        "document-enforcement-via-model-only"
    ]
    assert "cross-language structural guarantee" in result.warnings[0].message
