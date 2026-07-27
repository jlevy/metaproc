"""Tests for write-boundary validation (P3.5.5 regression coverage).

Covers the typed ``WriteBoundaryOverlapError`` raised by ``build_plan`` when two
agent steps declare writes into an overlapping tree. The 2026-04-23 earnings
``learn`` process shipped such an overlap (``propose-form-improvements`` ->
``process/predict/<v>/<v>-proposal.md`` vs ``apply-form-improvements`` ->
``process/predict/<v>/``). The source-side fix landed separately; this test
pins the framework-level guarantee so the bug class cannot recur as a soft
warning.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from metaproc.engine.write_boundary import (
    WriteBoundaryOverlap,
    WriteBoundaryOverlapError,
    WriteTarget,
    find_write_boundary_overlaps,
    targets_overlap,
)
from metaproc.models.authored import IOSpec, ProcessSpec, ProcessStep


def _agent_step(
    step_id: str,
    output_path: str,
    *,
    kind: Literal["file", "directory", "stream"] = "file",
) -> ProcessStep:
    return ProcessStep(
        id=step_id,
        mode="agent",
        outputs={"out": IOSpec(path=output_path, kind=kind)},
    )


class TestFindWriteBoundaryOverlaps:
    def test_file_nested_inside_directory_is_overlap(self) -> None:
        """The 2026-04-23 learn-process pattern: one step writes a file, a second
        step writes the containing directory. Must be detected as an overlap."""
        spec = ProcessSpec(
            name="learn-regression",
            steps=[
                _agent_step(
                    "propose-form-improvements",
                    "process/predict/v2/v2-proposal.md",
                    kind="file",
                ),
                _agent_step(
                    "apply-form-improvements",
                    "process/predict/v2/",
                    kind="directory",
                ),
            ],
        )
        step_outputs = {step.id: dict(step.outputs or {}) for step in spec.steps}

        overlaps = find_write_boundary_overlaps(spec, params={}, step_outputs=step_outputs)

        assert len(overlaps) == 1
        overlap = overlaps[0]
        assert overlap.left_step == "apply-form-improvements"
        assert overlap.right_step == "propose-form-improvements"
        assert "process/predict/v2" in str(overlap.left_path)
        assert "v2-proposal.md" in str(overlap.right_path)

    def test_disjoint_paths_no_overlap(self) -> None:
        spec = ProcessSpec(
            name="disjoint",
            steps=[
                _agent_step("a", "runs/a/out.md", kind="file"),
                _agent_step("b", "runs/b/out.md", kind="file"),
            ],
        )
        step_outputs = {step.id: dict(step.outputs or {}) for step in spec.steps}

        overlaps = find_write_boundary_overlaps(spec, params={}, step_outputs=step_outputs)

        assert overlaps == []

    def test_per_run_artifact_tree_no_overlap(self) -> None:
        """Recommended fix: move overlapping output into {{run.dir}}/... The
        post-fix layout must not trip the validator."""
        spec = ProcessSpec(
            name="post-fix-learn",
            steps=[
                _agent_step(
                    "propose-form-improvements",
                    "runs/myrun/learn/default/v2-proposal.md",
                    kind="file",
                ),
                _agent_step(
                    "apply-form-improvements",
                    "process/predict/v2/",
                    kind="directory",
                ),
            ],
        )
        step_outputs = {step.id: dict(step.outputs or {}) for step in spec.steps}

        overlaps = find_write_boundary_overlaps(spec, params={}, step_outputs=step_outputs)

        assert overlaps == []


class TestWriteBoundaryOverlapError:
    def test_message_lists_each_overlap(self) -> None:
        overlaps = [
            WriteBoundaryOverlap(
                left_step="propose",
                right_step="apply",
                left_path=Path("process/predict/v2/v2-proposal.md"),
                right_path=Path("process/predict/v2"),
            )
        ]

        err = WriteBoundaryOverlapError(overlaps)

        assert "invalid agent write boundaries" in str(err)
        assert "propose" in str(err)
        assert "apply" in str(err)
        assert "v2-proposal.md" in str(err)
        assert "Suggested fix" in str(err)
        assert "{{run.dir}}" in str(err)

    def test_is_value_error_subclass(self) -> None:
        """Browser + CLI catch-ValueError paths must still trip."""
        overlaps = [
            WriteBoundaryOverlap(
                left_step="a",
                right_step="b",
                left_path=Path("x"),
                right_path=Path("x/y"),
            )
        ]
        err = WriteBoundaryOverlapError(overlaps)

        with pytest.raises(ValueError):
            raise err

    def test_exposes_structured_overlaps(self) -> None:
        overlaps = [
            WriteBoundaryOverlap(
                left_step="propose",
                right_step="apply",
                left_path=Path("process/predict/v2/v2-proposal.md"),
                right_path=Path("process/predict/v2"),
            )
        ]

        err = WriteBoundaryOverlapError(overlaps)

        assert err.overlaps == overlaps
        assert err.overlaps[0].left_step == "propose"


class TestTargetsOverlap:
    def test_file_under_tree_overlaps(self) -> None:
        tree = WriteTarget(path=Path("process/predict/v2"), kind="tree")
        file = WriteTarget(path=Path("process/predict/v2/proposal.md"), kind="exact")
        assert targets_overlap(tree, file) is True
        assert targets_overlap(file, tree) is True

    def test_same_exact_path_overlaps(self) -> None:
        a = WriteTarget(path=Path("x/y.md"), kind="exact")
        b = WriteTarget(path=Path("x/y.md"), kind="exact")
        assert targets_overlap(a, b) is True

    def test_sibling_files_no_overlap(self) -> None:
        a = WriteTarget(path=Path("x/a.md"), kind="exact")
        b = WriteTarget(path=Path("x/b.md"), kind="exact")
        assert targets_overlap(a, b) is False
