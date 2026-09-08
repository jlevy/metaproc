"""Unit tests for `ResourcesDocument` and friends (B2, spec §4.4.2 / §4.4.4)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from softschema import SchemaProfile, validate_artifact

from metaproc.models.resources import (
    RESOURCES_DOCUMENT_CONTRACT,
    SCHEMA_V1,
    SCHEMA_V2,
    LogSummary,
    Metrics,
    MetricsV1,
    Node,
    PrefixRollup,
    ResourcesDocument,
    ResourcesDocumentV1,
    ResourcesDocumentV2,
    SourceLog,
    SourceRef,
    read_resources_document_json,
)
from metaproc.plugins.registry import PluginRegistryImpl


def _ts() -> datetime:
    return datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC)


def _root_with_one_step() -> Node:
    step = Node(
        node_type="step",
        node_id="normalize-items",
        label="normalize-items",
        parent_id="process:root",
        self_metrics=Metrics(wall_time_s=0.0, actual_cost_usd=0.0),
        total_metrics=Metrics(wall_time_s=42.5, actual_cost_usd=6.10),
    )
    process = Node(
        node_type="process",
        node_id="process:root",
        label="root",
        parent_id="run-1",
        children=[step],
        self_metrics=Metrics(),
        total_metrics=Metrics(wall_time_s=42.5),
    )
    return Node(
        node_type="run",
        node_id="run-1",
        label="run-1",
        children=[process],
        total_metrics=Metrics(wall_time_s=42.5),
    )


def _doc(**overrides: object) -> ResourcesDocument:
    base: dict[str, object] = {
        "run_id": "run-1",
        "generated_at": _ts(),
        "source_events_path": ".logs/resource-events.jsonl",
        "hierarchy_root": _root_with_one_step(),
    }
    base.update(overrides)
    return ResourcesDocument.model_validate(base)


def _v1_node_payload(node: Node) -> dict[str, object]:
    metric_fields = set(MetricsV1.model_fields)
    return {
        "node_type": node.node_type,
        "node_id": node.node_id,
        "label": node.label,
        "parent_id": node.parent_id,
        "children": [_v1_node_payload(child) for child in node.children],
        "self_metrics": node.self_metrics.model_dump(include=metric_fields),
        "total_metrics": node.total_metrics.model_dump(include=metric_fields),
        "attribution": node.attribution,
        "log_summary": node.log_summary.model_dump(),
        "source_refs": [ref.model_dump() for ref in node.source_refs],
    }


def test_schema_field_persists_under_alias() -> None:
    """New writers persist the registered contract token under the ``schema`` alias."""
    doc = _doc()
    dumped = doc.model_dump(mode="json", by_alias=True)
    assert dumped["schema"] == RESOURCES_DOCUMENT_CONTRACT


def test_current_schema_field_pinned_to_registered_contract() -> None:
    with pytest.raises(ValidationError):
        ResourcesDocument.model_validate(
            {
                "schema": "metaproc.resources/v0",
                "run_id": "r",
                "generated_at": _ts(),
                "source_events_path": "x",
                "hierarchy_root": _root_with_one_step().model_dump(),
            }
        )


def test_reader_dispatches_current_and_strict_historical_contracts() -> None:
    v1_raw = json.dumps(
        {
            "schema": SCHEMA_V1,
            "run_id": "r",
            "generated_at": _ts().isoformat(),
            "source_events_path": ".logs/resource-events.jsonl",
            "hierarchy_root": _v1_node_payload(_root_with_one_step()),
        }
    )
    current_payload = _doc().model_dump(mode="json", by_alias=True)
    v2_payload = {**current_payload, "schema": SCHEMA_V2}

    assert isinstance(read_resources_document_json(v1_raw), ResourcesDocumentV1)
    assert isinstance(read_resources_document_json(json.dumps(v2_payload)), ResourcesDocumentV2)
    assert isinstance(read_resources_document_json(json.dumps(current_payload)), ResourcesDocument)


def test_resources_document_is_registered_as_standalone_contract(tmp_path: Path) -> None:
    path = tmp_path / "resources.json"
    path.write_text(_doc().model_dump_json(by_alias=True))
    registry = PluginRegistryImpl().softschemas

    contract = registry.resolve(RESOURCES_DOCUMENT_CONTRACT)
    assert contract is not None
    assert contract.model is ResourcesDocument
    assert contract.envelope_key is None
    assert contract.profile is SchemaProfile.pure_yaml

    validation = validate_artifact(
        path,
        contract_id=RESOURCES_DOCUMENT_CONTRACT,
        registry=registry,
    )
    assert validation.ok


def test_reader_rejects_unknown_schema_token() -> None:
    raw = _doc().model_dump(mode="json", by_alias=True)
    raw["schema"] = "metaproc.resources/v99"
    with pytest.raises(ValueError, match="resources schema token"):
        read_resources_document_json(json.dumps(raw))


def test_reader_rejects_missing_schema_token() -> None:
    raw = _doc().model_dump(mode="json", by_alias=True)
    raw.pop("schema")

    with pytest.raises(ValueError, match="missing resources schema token"):
        read_resources_document_json(json.dumps(raw))


def test_node_tree_is_recursive() -> None:
    root = _root_with_one_step()
    process = root.children[0]
    step = process.children[0]
    assert step.parent_id == "process:root"
    assert step.node_id == "normalize-items"
    assert root.children[0].children[0].label == "normalize-items"


def test_self_and_total_metrics_are_separate_fields() -> None:
    """Spec §1: parents must keep self overhead distinct from rolled subtree totals."""
    process = _root_with_one_step().children[0]
    assert process.self_metrics.wall_time_s is None
    assert process.total_metrics.wall_time_s == 42.5


def test_attribution_defaults_to_measured() -> None:
    n = Node(node_type="run", node_id="r", label="r")
    assert n.attribution == "measured"


def test_attribution_accepts_estimated_and_run_overhead() -> None:
    Node(node_type="run", node_id="r", label="r", attribution="estimated")
    Node(node_type="run", node_id="r", label="r", attribution="run_overhead")
    with pytest.raises(ValidationError):
        Node.model_validate(
            {"node_type": "run", "node_id": "r", "label": "r", "attribution": "bogus"}
        )


def test_taxonomy_rollups_persist_as_lists_not_tuple_keys() -> None:
    """Spec §4.4.4: PrefixRollup is a list-of-entries, not a dict keyed by tuple."""
    doc = _doc(
        taxonomy_rollups={
            "time_kind_path": [
                PrefixRollup(
                    path=["waiting"],
                    canonical="waiting",
                    metrics=Metrics(wall_time_s=42.5),
                ),
                PrefixRollup(
                    path=["waiting", "throttling"],
                    canonical="waiting > throttling",
                    metrics=Metrics(wall_time_s=4.5),
                ),
            ]
        }
    )
    dumped = json.loads(doc.model_dump_json(by_alias=True))
    rollups = dumped["taxonomy_rollups"]["time_kind_path"]
    assert isinstance(rollups, list)
    assert rollups[0]["path"] == ["waiting"]
    assert rollups[0]["canonical"] == "waiting"
    assert rollups[1]["canonical"] == "waiting > throttling"


def test_round_trip_serialisation() -> None:
    doc = _doc(
        source_logs=[
            SourceLog(
                kind="agent_log",
                path=".logs/x.jsonl",
                adapter="claude",
                summary=LogSummary(event_count=10, tool_call_count=3),
            )
        ],
        unattributed=Metrics(actual_cost_usd=0.05),
    )
    raw = doc.model_dump_json(by_alias=True)
    restored = ResourcesDocument.model_validate_json(raw)
    assert restored.run_id == "run-1"
    assert restored.source_logs[0].adapter == "claude"
    assert restored.unattributed.actual_cost_usd == 0.05


def test_unknown_top_level_field_rejected() -> None:
    with pytest.raises(ValidationError):
        ResourcesDocument.model_validate(
            {
                "schema": RESOURCES_DOCUMENT_CONTRACT,
                "run_id": "r",
                "generated_at": _ts(),
                "source_events_path": "x",
                "hierarchy_root": _root_with_one_step().model_dump(),
                "totally_made_up": "field",
            }
        )


def test_node_type_is_constrained() -> None:
    with pytest.raises(ValidationError):
        Node.model_validate({"node_type": "bogus", "node_id": "x", "label": "x"})


def test_source_log_summary_defaults_to_zero_counters() -> None:
    sl = SourceLog(kind="runpool_events", path=".logs/runpool-events.jsonl")
    assert sl.summary.event_count == 0
    assert sl.summary.error_count == 0


def test_source_ref_carries_byte_offset_and_size() -> None:
    """Spec §4.4.4: track path / offset / size / mtime for source freshness."""
    sr = SourceRef(
        kind="agent_log", path=".logs/x.jsonl", line_offset=42, size_bytes=1024, mtime_ns=12345
    )
    assert sr.line_offset == 42
    assert sr.size_bytes == 1024
    assert sr.mtime_ns == 12345
