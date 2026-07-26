"""Tests for the planner-side lane materializer."""

from __future__ import annotations

import pytest

from metaproc.engine.lane_expand import (
    count_task_instances,
    expand_step_task_instances,
    materialize_execution_lanes,
)
from metaproc.models.execution_profile import ExecutionProfileRegistry
from metaproc.models.lane import LaneMatrix, LaneSpec
from metaproc.models.plan import FanOut, ResolvedAdapter, ResolvedStep


@pytest.fixture
def registry() -> ExecutionProfileRegistry:
    return ExecutionProfileRegistry.load()


def _step(step_id: str, items: list[dict[str, str]] | None = None) -> ResolvedStep:
    fan_out = (
        FanOut(over="tickers", bind="ticker", source="-", items=items)
        if items is not None
        else None
    )
    return ResolvedStep(
        step_id=step_id,
        mode="agent",
        adapter=ResolvedAdapter(type="codex-cli"),
        fan_out=fan_out,
    )


class TestMaterializeExecutionLanes:
    def test_none_matrix_with_fallback_synthesizes_single_lane(
        self, registry: ExecutionProfileRegistry
    ) -> None:
        run_profile = registry.resolve("codex-gpt55")
        lanes = materialize_execution_lanes(
            None,
            registry=registry,
            fallback_profile=run_profile,
            fallback_artifact_namespace="example-artifacts-v0",
        )
        assert len(lanes) == 1
        lane = lanes[0]
        assert lane.lane_id == "codex-gpt55"
        assert lane.execution_profile == "codex-gpt55"
        assert lane.adapter == "codex-cli"
        assert lane.replica_index == 0
        assert lane.artifact_namespace == "example-artifacts-v0"

    def test_none_matrix_without_fallback_returns_empty(
        self, registry: ExecutionProfileRegistry
    ) -> None:
        assert materialize_execution_lanes(None, registry=registry) == []

    def test_shorthand_matrix_expands(self, registry: ExecutionProfileRegistry) -> None:
        matrix = LaneMatrix(profiles=["codex-gpt55", "claude-opus"], replicas=2)
        lanes = materialize_execution_lanes(matrix, registry=registry)
        assert [lane.lane_id for lane in lanes] == [
            "codex-gpt55",
            "codex-gpt55-r1",
            "claude-opus",
            "claude-opus-r1",
        ]
        assert [lane.adapter for lane in lanes] == [
            "codex-cli",
            "codex-cli",
            "claude-code-cli",
            "claude-code-cli",
        ]

    def test_explicit_lane_id_pinned(self, registry: ExecutionProfileRegistry) -> None:
        matrix = LaneMatrix(
            lanes=[
                LaneSpec(
                    execution_profile="codex-gpt55",
                    lane_id="primary",
                    comparison_role="primary",
                ),
                LaneSpec(
                    execution_profile="claude-opus",
                    lane_id="comparison",
                    comparison_role="comparison",
                ),
            ]
        )
        lanes = materialize_execution_lanes(matrix, registry=registry)
        assert [lane.lane_id for lane in lanes] == ["primary", "comparison"]
        assert [lane.comparison_role for lane in lanes] == ["primary", "comparison"]

    def test_duplicate_lane_ids_rejected(self, registry: ExecutionProfileRegistry) -> None:
        matrix = LaneMatrix(
            lanes=[
                LaneSpec(execution_profile="codex-gpt55"),
                LaneSpec(execution_profile="codex-gpt55"),
            ]
        )
        with pytest.raises(ValueError, match="duplicate lane_id"):
            materialize_execution_lanes(matrix, registry=registry)

    def test_unknown_profile_rejected(self, registry: ExecutionProfileRegistry) -> None:
        matrix = LaneMatrix(lanes=[LaneSpec(execution_profile="nonexistent-profile")])
        with pytest.raises(ValueError, match="lane_matrix lane"):
            materialize_execution_lanes(matrix, registry=registry)

    def test_overrides_rejected_until_execution_wires_them(
        self, registry: ExecutionProfileRegistry
    ) -> None:
        """Non-empty lane overrides are refused at materialization.

        Bead internal-reference (internal review P2): the lane id hashes overrides
        in (so two lanes that differ only by overrides get distinct
        ids), but the execution path does not yet route per-lane
        adapter config. Accepting non-empty overrides silently would
        produce trace spans that group cleanly by lane_id while every
        lane actually ran with the same config. Refuse here; revisit
        when the same-runpool multi-lane drive lands and per-lane
        configs flow through.
        """
        matrix = LaneMatrix(
            lanes=[
                LaneSpec(execution_profile="codex-gpt55"),
                LaneSpec(
                    execution_profile="codex-gpt55",
                    overrides={"tools": ["WebSearch"]},
                ),
            ]
        )
        with pytest.raises(ValueError, match="overrides are not yet wired"):
            materialize_execution_lanes(matrix, registry=registry)

    def test_artifact_namespace_override_respected(
        self, registry: ExecutionProfileRegistry
    ) -> None:
        matrix = LaneMatrix(
            lanes=[
                LaneSpec(
                    execution_profile="codex-gpt55",
                    artifact_namespace="codex-output",
                ),
                LaneSpec(execution_profile="claude-opus"),
            ]
        )
        lanes = materialize_execution_lanes(
            matrix,
            registry=registry,
            fallback_artifact_namespace="default-output",
        )
        assert lanes[0].artifact_namespace == "codex-output"
        assert lanes[1].artifact_namespace == "default-output"


class TestExpandStepTaskInstances:
    def test_fan_out_cross_product(self, registry: ExecutionProfileRegistry) -> None:
        lanes = materialize_execution_lanes(
            LaneMatrix(profiles=["codex-gpt55", "claude-opus"]),
            registry=registry,
        )
        step = _step(
            "predict",
            items=[{"ticker": "CAVA", "sector": "consumer"}, {"ticker": "MNDY"}],
        )
        instances = expand_step_task_instances(step, lanes)
        assert len(instances) == 4
        task_ids = [t.task_id for t in instances]
        assert "predict:codex-gpt55:CAVA" in task_ids
        assert "predict:codex-gpt55:MNDY" in task_ids
        assert "predict:claude-opus:CAVA" in task_ids
        assert "predict:claude-opus:MNDY" in task_ids
        cava_codex = next(t for t in instances if t.task_id == "predict:codex-gpt55:CAVA")
        assert cava_codex.item_context == {"ticker": "CAVA", "sector": "consumer"}
        assert cava_codex.execution_profile == "codex-gpt55"
        assert cava_codex.replica_index == 0

    def test_non_fan_out_step_has_single_sentinel_instance(
        self, registry: ExecutionProfileRegistry
    ) -> None:
        lanes = materialize_execution_lanes(
            LaneMatrix(profiles=["codex-gpt55"]),
            registry=registry,
        )
        step = _step("scaffold", items=None)
        instances = expand_step_task_instances(step, lanes)
        assert len(instances) == 1
        assert instances[0].task_id == "scaffold:codex-gpt55:_step"
        assert instances[0].item_key == "_step"

    def test_empty_items_yield_no_instances(self, registry: ExecutionProfileRegistry) -> None:
        lanes = materialize_execution_lanes(
            LaneMatrix(profiles=["codex-gpt55"]),
            registry=registry,
        )
        step = _step("predict", items=[])
        assert expand_step_task_instances(step, lanes) == []

    def test_no_lanes_yields_no_instances(self) -> None:
        step = _step("predict", items=[{"ticker": "CAVA"}])
        assert expand_step_task_instances(step, []) == []


class TestCountTaskInstances:
    def test_per_step_counts(self, registry: ExecutionProfileRegistry) -> None:
        lanes = materialize_execution_lanes(
            LaneMatrix(profiles=["codex-gpt55", "claude-opus"]),
            registry=registry,
        )
        steps = [
            _step("scaffold"),
            _step(
                "predict",
                items=[{"ticker": "CAVA"}, {"ticker": "MNDY"}, {"ticker": "NVDA"}],
            ),
        ]
        assert count_task_instances(steps, lanes) == {"scaffold": 2, "predict": 6}
