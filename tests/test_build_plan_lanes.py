"""Integration tests for ``build_plan`` lane-matrix expansion."""

from __future__ import annotations

import tempfile
import textwrap
from pathlib import Path
from typing import Any

from metaproc.engine.build_plan import build_plan
from metaproc.models.authored import ProcessSpec


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip())


def _bare_spec_with_matrix(lane_matrix: dict[str, Any] | None = None) -> ProcessSpec:
    payload: dict[str, Any] = {
        "name": "lane-smoke",
        "defaults": {"default_execution_profile": "codex-gpt55"},
        "steps": [
            {
                "id": "scaffold",
                "mode": "code",
                "handler": "noop",
            },
        ],
    }
    if lane_matrix is not None:
        payload["lane_matrix"] = lane_matrix
    return ProcessSpec.model_validate(payload)


def test_build_plan_synthesizes_single_lane_for_default_profile() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        process_path = Path(tmpdir) / "lane-smoke.process.md"
        _write(process_path, "---\nprocess:\n  name: lane-smoke\n---\n")

        spec = _bare_spec_with_matrix()
        plan = build_plan(spec, {}, process_path=process_path)

        assert plan.execution_profile == "codex-gpt55"
        assert plan.lane_matrix is None
        assert len(plan.execution_lanes) == 1
        lane = plan.execution_lanes[0]
        assert lane.lane_id == "codex-gpt55"
        assert lane.execution_profile == "codex-gpt55"
        assert lane.adapter == "codex-cli"


def test_build_plan_expands_authored_lane_matrix() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        process_path = Path(tmpdir) / "lane-smoke.process.md"
        _write(process_path, "---\nprocess:\n  name: lane-smoke\n---\n")

        spec = _bare_spec_with_matrix(
            {
                "lanes": [
                    {
                        "execution_profile": "codex-gpt55",
                        "comparison_role": "primary",
                    },
                    {
                        "execution_profile": "claude-opus",
                        "comparison_role": "comparison",
                    },
                ]
            }
        )
        plan = build_plan(spec, {}, process_path=process_path)

        assert plan.lane_matrix is not None
        assert len(plan.execution_lanes) == 2
        assert [lane.lane_id for lane in plan.execution_lanes] == [
            "codex-gpt55",
            "claude-opus",
        ]
        assert [lane.comparison_role for lane in plan.execution_lanes] == [
            "primary",
            "comparison",
        ]


def test_build_plan_replicas_shorthand() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        process_path = Path(tmpdir) / "lane-smoke.process.md"
        _write(process_path, "---\nprocess:\n  name: lane-smoke\n---\n")

        spec = _bare_spec_with_matrix({"profiles": ["codex-gpt55"], "replicas": 3})
        plan = build_plan(spec, {}, process_path=process_path)

        assert [lane.lane_id for lane in plan.execution_lanes] == [
            "codex-gpt55",
            "codex-gpt55-r1",
            "codex-gpt55-r2",
        ]


def test_build_plan_propagates_artifact_namespace_fallback() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        process_path = Path(tmpdir) / "lane-smoke.process.md"
        _write(process_path, "---\nprocess:\n  name: lane-smoke\n---\n")

        spec = _bare_spec_with_matrix(
            {
                "lanes": [
                    {"execution_profile": "codex-gpt55"},
                    {
                        "execution_profile": "claude-opus",
                        "artifact_namespace": "claude-output",
                    },
                ]
            }
        )
        plan = build_plan(
            spec,
            {},
            process_path=process_path,
            artifact_namespace="primary-output",
        )

        assert plan.artifact_namespace == "primary-output"
        assert plan.execution_lanes[0].artifact_namespace == "primary-output"
        assert plan.execution_lanes[1].artifact_namespace == "claude-output"


def test_build_plan_dumps_lane_data_via_alias() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        process_path = Path(tmpdir) / "lane-smoke.process.md"
        _write(process_path, "---\nprocess:\n  name: lane-smoke\n---\n")

        spec = _bare_spec_with_matrix({"lanes": [{"execution_profile": "codex-gpt55"}]})
        plan = build_plan(spec, {}, process_path=process_path)
        dumped = plan.model_dump(by_alias=True, exclude_none=True)
        assert "execution_lanes" in dumped
        assert dumped["execution_lanes"][0]["lane_id"] == "codex-gpt55"
        assert dumped["lane_matrix"]["lanes"][0]["execution_profile"] == "codex-gpt55"
