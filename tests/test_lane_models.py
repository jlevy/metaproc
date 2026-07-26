"""Unit tests for the lane model and lane-id derivation."""

from __future__ import annotations

import pytest

from metaproc.models.lane import (
    ExecutionLane,
    LaneMatrix,
    LaneSpec,
    TaskInstance,
    derive_lane_id,
    derive_task_id,
)


class TestDeriveLaneId:
    def test_degenerate_returns_profile_name(self) -> None:
        assert derive_lane_id(execution_profile="codex-gpt55", replica_index=0) == "codex-gpt55"

    def test_replica_index_appended(self) -> None:
        assert derive_lane_id(execution_profile="codex-gpt55", replica_index=2) == "codex-gpt55-r2"

    def test_overrides_produce_stable_hash_suffix(self) -> None:
        a = derive_lane_id(
            execution_profile="codex-gpt55",
            replica_index=0,
            overrides={"tools": ["WebSearch"]},
        )
        b = derive_lane_id(
            execution_profile="codex-gpt55",
            replica_index=0,
            overrides={"tools": ["WebSearch"]},
        )
        assert a == b
        assert a.startswith("codex-gpt55-o")
        assert len(a.split("-o")[1]) == 8

    def test_override_key_order_does_not_affect_id(self) -> None:
        a = derive_lane_id(
            execution_profile="codex-gpt55",
            replica_index=0,
            overrides={"a": 1, "b": 2},
        )
        b = derive_lane_id(
            execution_profile="codex-gpt55",
            replica_index=0,
            overrides={"b": 2, "a": 1},
        )
        assert a == b

    def test_different_overrides_produce_different_ids(self) -> None:
        a = derive_lane_id(execution_profile="codex-gpt55", overrides={"model": "gpt-5.5"})
        b = derive_lane_id(execution_profile="codex-gpt55", overrides={"model": "gpt-5.5-mini"})
        assert a != b

    def test_unsafe_profile_chars_get_sanitized(self) -> None:
        assert derive_lane_id(execution_profile="codex/gpt55!") == "codex_gpt55_"

    def test_empty_profile_rejected(self) -> None:
        with pytest.raises(ValueError, match="execution_profile is required"):
            derive_lane_id(execution_profile="", replica_index=0)


class TestDeriveTaskId:
    def test_format(self) -> None:
        assert (
            derive_task_id(step_id="predict", lane_id="codex-gpt55", item_key="CAVA")
            == "predict:codex-gpt55:CAVA"
        )


class TestLaneSpec:
    def test_minimal(self) -> None:
        spec = LaneSpec(execution_profile="codex-gpt55")
        assert spec.execution_profile == "codex-gpt55"
        assert spec.replica_index == 0
        assert spec.overrides == {}

    def test_negative_replica_rejected(self) -> None:
        with pytest.raises(ValueError, match="replica_index must be >= 0"):
            LaneSpec(execution_profile="codex-gpt55", replica_index=-1)

    def test_invalid_lane_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="lane_id must match"):
            LaneSpec(execution_profile="codex-gpt55", lane_id="codex/bad")

    def test_explicit_lane_id_accepted(self) -> None:
        spec = LaneSpec(execution_profile="codex-gpt55", lane_id="codex_A")
        assert spec.lane_id == "codex_A"


class TestLaneMatrix:
    def test_empty_matrix(self) -> None:
        matrix = LaneMatrix()
        assert matrix.lanes == []

    def test_shorthand_expansion(self) -> None:
        matrix = LaneMatrix(profiles=["codex-gpt55", "claude-opus"], replicas=2)
        assert len(matrix.lanes) == 4
        expanded = [(lane.execution_profile, lane.replica_index) for lane in matrix.lanes]
        assert expanded == [
            ("codex-gpt55", 0),
            ("codex-gpt55", 1),
            ("claude-opus", 0),
            ("claude-opus", 1),
        ]
        # Shorthand fields are cleared after expansion so round-trip dump is canonical.
        assert matrix.profiles == []
        assert matrix.replicas == 1

    def test_shorthand_plus_explicit(self) -> None:
        matrix = LaneMatrix(
            profiles=["codex-gpt55"],
            replicas=1,
            lanes=[
                LaneSpec(
                    execution_profile="claude-opus",
                    comparison_role="comparison",
                )
            ],
        )
        assert len(matrix.lanes) == 2
        assert matrix.lanes[0].execution_profile == "codex-gpt55"
        assert matrix.lanes[1].execution_profile == "claude-opus"
        assert matrix.lanes[1].comparison_role == "comparison"

    def test_replicas_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="replicas must be >= 1"):
            LaneMatrix(profiles=["codex-gpt55"], replicas=0)


class TestExecutionLane:
    def test_round_trip(self) -> None:
        lane = ExecutionLane(
            lane_id="codex-gpt55",
            execution_profile="codex-gpt55",
            adapter="codex-cli",
            replica_index=0,
        )
        data = lane.model_dump()
        again = ExecutionLane.model_validate(data)
        assert again == lane


class TestTaskInstance:
    def test_round_trip(self) -> None:
        task = TaskInstance(
            task_id="predict:codex-gpt55:CAVA",
            step_id="predict",
            lane_id="codex-gpt55",
            item_key="CAVA",
            execution_profile="codex-gpt55",
            item_context={"ticker": "CAVA"},
        )
        data = task.model_dump()
        again = TaskInstance.model_validate(data)
        assert again == task
