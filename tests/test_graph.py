"""Unit tests for engine/graph.py — step dependency graph utilities."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Literal

import pytest

from metaproc.engine.build_plan import build_plan
from metaproc.engine.graph import (
    detect_cycles,
    downstream,
    propagate_failure,
    topo_sort,
    validate_step_graph,
)
from metaproc.models.authored import ProcessSpec
from metaproc.models.plan import ResolvedAdapter, ResolvedStep


def _step(
    id: str,
    needs: list[str] | None = None,
    on_failure: Literal["block", "continue"] = "block",
) -> ResolvedStep:
    """Minimal ResolvedStep for graph tests."""
    return ResolvedStep(
        step_id=id,
        mode="agent",
        adapter=ResolvedAdapter(type="test", config={}),
        needs=needs or [],
        on_failure=on_failure,
    )


# ── Fixtures: real process DAGs ──────────────────────────────────


def _predict_steps() -> list[ResolvedStep]:
    return [
        _step("scaffold-day"),
        _step("generate-base-rates", ["scaffold-day"]),
        _step("generate-research-packet", ["scaffold-day"]),
        _step("mine-adhoc", ["scaffold-day", "generate-base-rates"]),
        _step("retrieve-precedent", ["scaffold-day", "generate-base-rates", "mine-adhoc"]),
        _step(
            "predict-ticker",
            [
                "scaffold-day",
                "generate-base-rates",
                "generate-research-packet",
                "retrieve-precedent",
            ],
        ),
        _step("qa-check", ["predict-ticker"]),
        _step("create-prediction-summary", ["predict-ticker"]),
        _step("create-ops-review", ["predict-ticker"]),
        _step("generate-usage-report", ["predict-ticker", "qa-check"]),
        _step(
            "create-run-overview",
            ["qa-check", "create-prediction-summary", "generate-usage-report"],
        ),
    ]


def _mine_steps() -> list[ResolvedStep]:
    return [
        _step("generate-record"),
        _step("qa-check", ["generate-record"]),
        _step("review-batch", ["generate-record"]),
        _step("create-mine-summary", ["generate-record"]),
        _step("generate-usage-report", ["generate-record"]),
        _step(
            "create-mine-overview",
            ["qa-check", "create-mine-summary", "generate-usage-report"],
        ),
    ]


def _retro_steps() -> list[ResolvedStep]:
    return [
        _step("scaffold-retro"),
        _step("integrity-check", ["scaffold-retro"]),
        _step("predict-retro", ["scaffold-retro"]),
        _step("qa-check", ["predict-retro"]),
        _step("create-retro-summary", ["predict-retro"]),
        _step("create-leakage-summary", ["predict-retro"]),
        _step("create-integrity-check-summary", ["integrity-check"]),
        _step("generate-usage-report", ["predict-retro"]),
        _step(
            "create-run-overview",
            [
                "qa-check",
                "create-retro-summary",
                "generate-usage-report",
                "create-leakage-summary",
                "create-integrity-check-summary",
            ],
        ),
    ]


# ── validate_step_graph ──────────────────────────────────────────


class TestValidateStepGraph:
    def test_linear_chain_valid(self):
        steps = [_step("a"), _step("b", ["a"]), _step("c", ["b"])]
        assert validate_step_graph(steps) == []

    def test_diamond_valid(self):
        steps = [
            _step("a"),
            _step("b", ["a"]),
            _step("c", ["a"]),
            _step("d", ["b", "c"]),
        ]
        assert validate_step_graph(steps) == []

    def test_duplicate_step_ids(self):
        steps = [_step("qa-check"), _step("qa-check")]
        errors = validate_step_graph(steps)
        assert len(errors) == 1
        assert "duplicate step id" in errors[0]
        assert "qa-check" in errors[0]

    def test_dangling_reference(self):
        steps = [_step("a"), _step("b", ["nonexistent-step"])]
        errors = validate_step_graph(steps)
        assert any("nonexistent-step" in e for e in errors)

    def test_self_cycle(self):
        steps = [_step("a", ["a"])]
        errors = validate_step_graph(steps)
        assert any("cycle" in e for e in errors)

    def test_multi_step_cycle(self):
        steps = [
            _step("a", ["c"]),
            _step("b", ["a"]),
            _step("c", ["b"]),
        ]
        errors = validate_step_graph(steps)
        assert any("cycle" in e for e in errors)

    def test_predict_dag_valid(self):
        assert validate_step_graph(_predict_steps()) == []

    def test_mine_dag_valid(self):
        assert validate_step_graph(_mine_steps()) == []

    def test_retro_dag_valid(self):
        assert validate_step_graph(_retro_steps()) == []

    def test_aggregated_errors(self):
        """Multiple errors are returned together."""
        steps = [
            _step("a", ["missing1"]),
            _step("a", ["missing2"]),
        ]
        errors = validate_step_graph(steps)
        assert len(errors) >= 2  # duplicate + dangling refs


# ── detect_cycles ────────────────────────────────────────────────


class TestDetectCycles:
    def test_no_cycles(self):
        steps = [_step("a"), _step("b", ["a"]), _step("c", ["b"])]
        assert detect_cycles(steps) == []

    def test_self_cycle(self):
        steps = [_step("a", ["a"])]
        cycles = detect_cycles(steps)
        assert len(cycles) == 1

    def test_triangle_cycle(self):
        steps = [
            _step("a", ["c"]),
            _step("b", ["a"]),
            _step("c", ["b"]),
        ]
        cycles = detect_cycles(steps)
        assert len(cycles) >= 1
        # All three nodes should appear in the cycle
        all_nodes = {n for cycle in cycles for n in cycle}
        assert {"a", "b", "c"} <= all_nodes


# ── downstream ───────────────────────────────────────────────────


class TestDownstream:
    def test_linear_chain(self):
        steps = [_step("a"), _step("b", ["a"]), _step("c", ["b"])]
        assert downstream(steps, "a") == ["b", "c"]

    def test_root_with_no_dependents(self):
        steps = [_step("a"), _step("b", ["a"]), _step("c", ["b"])]
        assert downstream(steps, "c") == []

    def test_diamond(self):
        steps = [
            _step("a"),
            _step("b", ["a"]),
            _step("c", ["a"]),
            _step("d", ["b", "c"]),
        ]
        result = downstream(steps, "a")
        assert set(result) == {"b", "c", "d"}

    def test_predict_downstream_from_predict_ticker(self):
        """downstream from a map step returns correct reduce steps."""
        steps = _predict_steps()
        result = downstream(steps, "predict-ticker")
        expected = {
            "qa-check",
            "create-prediction-summary",
            "create-ops-review",
            "generate-usage-report",
            "create-run-overview",
        }
        assert set(result) == expected

    def test_predict_downstream_from_scaffold(self):
        steps = _predict_steps()
        result = downstream(steps, "scaffold-day")
        # Everything except scaffold-day itself
        all_ids = {s.step_id for s in steps} - {"scaffold-day"}
        assert set(result) == all_ids


# ── topo_sort ────────────────────────────────────────────────────


class TestTopoSort:
    def test_linear_chain(self):
        steps = [_step("a"), _step("b", ["a"]), _step("c", ["b"])]
        levels = topo_sort(steps)
        assert levels == [["a"], ["b"], ["c"]]

    def test_diamond(self):
        steps = [
            _step("a"),
            _step("b", ["a"]),
            _step("c", ["a"]),
            _step("d", ["b", "c"]),
        ]
        levels = topo_sort(steps)
        assert levels[0] == ["a"]
        assert sorted(levels[1]) == ["b", "c"]
        assert levels[2] == ["d"]

    def test_no_deps(self):
        steps = [_step("a"), _step("b"), _step("c")]
        levels = topo_sort(steps)
        assert levels == [["a", "b", "c"]]

    def test_predict_dag_levels(self):
        steps = _predict_steps()
        levels = topo_sort(steps)
        # Level 0: scaffold-day (no deps)
        assert levels[0] == ["scaffold-day"]
        # All steps should appear exactly once
        all_sorted = [sid for level in levels for sid in level]
        assert sorted(all_sorted) == sorted(s.step_id for s in steps)

    def test_mine_dag_levels(self):
        steps = _mine_steps()
        levels = topo_sort(steps)
        assert levels[0] == ["generate-record"]
        # Level 1: qa-check, review-batch, create-mine-summary, generate-usage-report
        assert len(levels[1]) == 4
        # Level 2: create-mine-overview
        assert levels[2] == ["create-mine-overview"]

    def test_step_ids_subset(self):
        """step_ids restricts the sort; external deps treated as satisfied."""
        steps = [_step("a"), _step("b", ["a"]), _step("c", ["b"])]
        # Only sort b and c; a is outside the subset, treated as satisfied
        levels = topo_sort(steps, step_ids={"b", "c"})
        assert levels == [["b"], ["c"]]

    def test_step_ids_subset_with_diamond(self):
        steps = [
            _step("a"),
            _step("b", ["a"]),
            _step("c", ["a"]),
            _step("d", ["b", "c"]),
        ]
        # Only b and d; a and c are external
        levels = topo_sort(steps, step_ids={"b", "d"})
        assert levels == [["b"], ["d"]]


# ── build_plan integration ───────────────────────────────────────


class TestBuildPlanNeedsValidation:
    """Integration tests: build_plan raises on invalid needs graphs."""

    def _build(self, steps_data: list[dict[str, object]]) -> None:

        with tempfile.TemporaryDirectory() as tmpdir:
            process_path = Path(tmpdir) / "test.process.md"
            process_path.parent.mkdir(parents=True, exist_ok=True)
            process_path.write_text("---\nprocess:\n  name: test\n---\n")

            normalized_steps: list[dict[str, object]] = []
            for step_data in steps_data:
                step_dict = dict(step_data)
                if step_dict.get("mode") == "agent" and "outputs" not in step_dict:
                    step_id = str(step_dict["id"])
                    step_dict["outputs"] = {
                        "main": {
                            "path": f"{step_id}.md",
                        }
                    }
                normalized_steps.append(step_dict)

            spec = ProcessSpec.model_validate(
                {
                    "name": "test",
                    "defaults": {"default_adapter": "claude-code-cli"},
                    "steps": normalized_steps,
                }
            )
            build_plan(spec, {}, process_path=process_path)

    def test_valid_needs_passes(self):
        self._build(
            [
                {"id": "a", "mode": "agent"},
                {"id": "b", "mode": "agent", "needs": ["a"]},
            ]
        )

    def test_dangling_reference_raises(self):
        with pytest.raises(ValueError, match="does not exist"):
            self._build(
                [
                    {"id": "a", "mode": "agent"},
                    {"id": "b", "mode": "agent", "needs": ["nonexistent"]},
                ]
            )

    def test_cycle_raises(self):
        with pytest.raises(ValueError, match="cycle"):
            self._build(
                [
                    {"id": "a", "mode": "agent", "needs": ["b"]},
                    {"id": "b", "mode": "agent", "needs": ["a"]},
                ]
            )

    def test_duplicate_ids_raises(self):
        with pytest.raises(ValueError, match="duplicate"):
            self._build(
                [
                    {"id": "qa-check", "mode": "agent"},
                    {"id": "qa-check", "mode": "agent"},
                ]
            )


# ── propagate_failure (on_failure: continue) ─────────────────────


class TestPropagateFailure:
    def test_default_block_propagates_to_all_transitive_dependents(self):
        steps = [
            _step("a"),
            _step("b", ["a"]),
            _step("c", ["b"]),
        ]
        assert propagate_failure(steps, "a") == ["b", "c"]

    def test_continue_marked_step_excluded(self):
        """The exact 2026-05-13 case: edge-assessment → run-stats where
        run-stats is marked on_failure=continue should mean run-stats
        is NOT blocked when edge-assessment fails."""
        steps = [
            _step("edge-assessment"),
            _step("run-stats", ["edge-assessment"], on_failure="continue"),
        ]
        assert propagate_failure(steps, "edge-assessment") == []

    def test_continue_step_alongside_blocked_step(self):
        """Mixed dependents: some default, some continue.
        Only the default ones get blocked."""
        steps = [
            _step("a"),
            _step("b", ["a"]),  # default → block
            _step("rollup", ["a"], on_failure="continue"),  # continue → run
        ]
        assert propagate_failure(steps, "a") == ["b"]

    def test_failed_step_with_no_dependents_returns_empty(self):
        steps = [_step("leaf")]
        assert propagate_failure(steps, "leaf") == []
