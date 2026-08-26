"""Tests for B7: ownership fields on `UsageBucket` + log-path owner derivation."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from metaproc.logutil.log_path_owner import (
    LogOwner,
    derive_owner,
    derive_owner_for_hierarchy,
)
from metaproc.models.resources import Node
from metaproc.models.usage import (
    UsageBucket,
    UsageReport,
    bucket_to_dict,
    usage_report_to_frontmatter,
)

# ── UsageBucket ownership fields ────────────────────────────────────


def test_usage_bucket_ownership_fields_default_to_none() -> None:
    bucket = UsageBucket()
    assert bucket.process_node_id is None
    assert bucket.step_node_id is None
    assert bucket.item_key is None
    assert bucket.worker_id is None


def test_bucket_to_dict_omits_null_ownership_fields() -> None:
    bucket = UsageBucket(input_tokens=1000)
    d = bucket_to_dict(bucket)
    assert "process_node_id" not in d
    assert "step_node_id" not in d
    assert "item_key" not in d
    assert "worker_id" not in d


def test_bucket_to_dict_includes_set_ownership_fields() -> None:
    bucket = UsageBucket(
        input_tokens=1000,
        process_node_id="process:root",
        step_node_id="normalize-items",
        item_key="AAPL",
        worker_id="w-3",
    )
    d = bucket_to_dict(bucket)
    assert d["process_node_id"] == "process:root"
    assert d["step_node_id"] == "normalize-items"
    assert d["item_key"] == "AAPL"
    assert d["worker_id"] == "w-3"


def test_usage_report_supports_by_step_aggregate() -> None:
    report = UsageReport(
        run_id="run-1",
        phase="mine",
        generated="2026-04-24",
        by_step={
            "normalize-items": UsageBucket(
                step_node_id="normalize-items",
                input_tokens=10000,
                output_tokens=2000,
            )
        },
    )

    fm = usage_report_to_frontmatter(report)
    assert "by_step" in fm
    by_step = cast(dict[str, dict[str, object]], fm["by_step"])
    assert by_step["normalize-items"]["input_tokens"] == 10000
    assert by_step["normalize-items"]["step_node_id"] == "normalize-items"


def test_usage_report_omits_by_step_when_empty() -> None:
    """Backwards-compat: persisted usage.md unchanged for runs that don't carry per-step rollups."""
    report = UsageReport(run_id="run-1", phase="mine", generated="2026-04-24")
    fm = usage_report_to_frontmatter(report)
    assert "by_step" not in fm


# ── derive_owner ────────────────────────────────────────────────────


def test_derive_owner_scalar_step(tmp_path: Path) -> None:
    log = tmp_path / "normalize-items" / ".logs" / "session.jsonl"
    log.parent.mkdir(parents=True)
    log.touch()
    owner = derive_owner(log, tmp_path)
    assert owner.step_node_id == "normalize-items"
    assert owner.process_node_id == "process:root"
    assert owner.item_key is None
    assert owner.variant is None


def test_derive_owner_fan_out_step(tmp_path: Path) -> None:
    log = tmp_path / "normalize-items" / "item-1" / ".logs" / "session.jsonl"
    log.parent.mkdir(parents=True)
    log.touch()
    owner = derive_owner(log, tmp_path)
    assert owner.step_node_id == "normalize-items"
    assert owner.item_key == "item-1"


def test_derive_owner_fan_out_with_variant(tmp_path: Path) -> None:
    log = tmp_path / "predict" / "sonnet" / "AAPL" / ".logs" / "session.jsonl"
    log.parent.mkdir(parents=True)
    log.touch()
    owner = derive_owner(log, tmp_path)
    assert owner.step_node_id == "predict"
    assert owner.variant == "sonnet"
    assert owner.item_key == "sonnet/AAPL"


def test_derive_owner_root_logs_dir(tmp_path: Path) -> None:
    log = tmp_path / ".logs" / "summary.jsonl"
    log.parent.mkdir(parents=True)
    log.touch()
    owner = derive_owner(log, tmp_path)
    assert owner.process_node_id == "process:root"
    assert owner.step_node_id is None
    assert owner.item_key is None


def test_derive_owner_handles_path_outside_run_dir(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    log = other / ".logs" / "session.jsonl"
    log.parent.mkdir(parents=True)
    log.touch()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    owner = derive_owner(log, run_dir)
    assert owner == LogOwner(process_node_id=None, step_node_id=None, item_key=None)


def test_derive_owner_treats_relative_log_path_as_run_dir_relative(tmp_path: Path) -> None:
    log = tmp_path / "predict" / "AAPL" / ".logs" / "session.jsonl"
    log.parent.mkdir(parents=True)
    log.touch()
    owner = derive_owner(log, tmp_path)
    assert owner.step_node_id == "predict"
    assert owner.item_key == "AAPL"


def test_derive_owner_supports_modern_task_log_layout(tmp_path: Path) -> None:
    log = tmp_path / ".logs" / "tasks" / "predict" / "AAPL" / "session.jsonl"
    log.parent.mkdir(parents=True)
    log.touch()

    owner = derive_owner(log, tmp_path)

    assert owner.step_node_id == "predict"
    assert owner.item_key == "AAPL"


def test_immutable_hierarchy_resolves_composite_task_log_without_spec(tmp_path: Path) -> None:
    log = tmp_path / "research" / ".logs" / "tasks" / "analyze" / "AAPL" / "session.jsonl"
    log.parent.mkdir(parents=True)
    log.touch()
    hierarchy = Node(
        node_type="run",
        node_id="run-1",
        label="run-1",
        children=[
            Node(
                node_type="process",
                node_id="process:root",
                label="root",
                children=[
                    Node(
                        node_type="step",
                        node_id="research",
                        label="research",
                        children=[
                            Node(
                                node_type="process",
                                node_id="process:research",
                                label="research",
                                children=[
                                    Node(
                                        node_type="step",
                                        node_id="research::analyze",
                                        label="analyze",
                                    )
                                ],
                            )
                        ],
                    )
                ],
            )
        ],
    )

    owner = derive_owner_for_hierarchy(log, tmp_path, hierarchy)

    assert owner.process_node_id == "process:research"
    assert owner.step_node_id == "research::analyze"
    assert owner.item_key == "AAPL"
