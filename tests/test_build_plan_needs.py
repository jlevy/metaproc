"""Integration tests — needs propagation against checked-in synthetic process specs.

Loads representative standalone fixtures and calls ``build_plan()``. Verifies
that needs propagation works and the checked-in DAGs pass validation (no
duplicate IDs, dangling refs, or cycles).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from metaproc.commands.helpers import load_process_spec
from metaproc.engine.build_plan import build_plan

REPO_ROOT = Path(__file__).resolve().parents[1]

PROCESS_PATHS = (
    REPO_ROOT / "tests/fixtures/fingerprint_smoke/fingerprint-smoke.process.md",
    REPO_ROOT / "process/self-test/smoke-adapter-claude.process.md",
    REPO_ROOT / "process/self-test/smoke-adapter-codex.process.md",
)


@pytest.mark.parametrize("process_path", PROCESS_PATHS, ids=lambda path: path.stem)
def test_synthetic_process_spec_passes_validation(process_path: Path) -> None:
    """Synthetic process specs have valid dependency graphs."""
    spec = load_process_spec(process_path)
    plan = build_plan(spec, {}, process_path=process_path)

    # Verify needs were propagated
    steps_with_needs = [s for s in plan.steps if s.needs]
    assert steps_with_needs, f"{process_path.name}: no steps have needs — propagation broken"


@pytest.mark.parametrize("process_path", PROCESS_PATHS, ids=lambda path: path.stem)
def test_synthetic_process_needs_match_step_ids(process_path: Path) -> None:
    """Every needs reference in each process spec resolves to an actual step ID."""
    spec = load_process_spec(process_path)
    plan = build_plan(spec, {}, process_path=process_path)

    all_ids = {s.step_id for s in plan.steps}
    for step in plan.steps:
        for dep in step.needs:
            assert dep in all_ids, (
                f"{process_path.name}: step {step.step_id!r} needs {dep!r} which is not a step"
            )
