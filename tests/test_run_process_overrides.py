"""Integration tests for the override resolver hook in _verify_ancestors.

Verifies that run-scoped overrides let the dependency resolver treat a
failed/missing ancestor as satisfied, respecting for_downstream scoping.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from metaproc.commands.run_process import _verify_ancestors
from metaproc.io.overrides import upsert_override
from metaproc.io.state_io import write_status_at
from metaproc.io.state_io import write_status_at as _ws
from metaproc.models.authored import ProcessSpec, ProcessStep
from metaproc.models.plan import Plan, ResolvedAdapter, ResolvedStep
from metaproc.models.runtime import StatusRecord
from metaproc.models.runtime import StatusRecord as _SR
from metaproc.paths import STATE_DIR, TASKS_SUBDIR

# ── Helpers (mirrored from test_run_process.py) ────────────────────


def _step(
    id: str,
    needs: list[str] | None = None,
    mode: Literal["code", "agent", "composite", "manual"] = "code",
) -> ResolvedStep:
    return ResolvedStep(
        step_id=id,
        mode=mode,
        adapter=ResolvedAdapter(type="test", config={}),
        needs=needs or [],
    )


def _make_plan(*steps: ResolvedStep) -> Plan:
    return Plan(
        generated_at="2026-04-24T00:00:00",
        process="test",
        params={},
        steps=list(steps),
    )


def _make_spec(*step_ids: str) -> ProcessSpec:
    return ProcessSpec(
        name="test",
        steps=[ProcessStep(id=sid, mode="agent") for sid in step_ids],
    )


def _write_failed_status(run_dir: Path, step_id: str) -> None:
    state_dir = run_dir / STATE_DIR / TASKS_SUBDIR / step_id
    state_dir.mkdir(parents=True, exist_ok=True)
    record = StatusRecord(
        run_id="test/run1",
        step_id=step_id,
        item={"step": step_id},
        state="failed",
        started_at="2026-04-24T00:00:00",
        completed_at="2026-04-24T00:01:00",
        error="test error",
    )
    write_status_at(state_dir, record)


# ── Two-step plan: A -> B ─────────────────────────────────────────


def _ab_plan() -> tuple[Plan, ProcessSpec]:
    """A depends on nothing, B depends on A."""
    plan = _make_plan(
        _step("a"),
        _step("b", ["a"]),
    )
    spec = _make_spec("a", "b")
    return plan, spec


# ── Tests ──────────────────────────────────────────────────────────


class TestVerifyAncestorsWithOverrides:
    def test_failed_ancestor_blocks_downstream(self, tmp_path: Path) -> None:
        """Baseline: failed A blocks B when no override exists."""
        plan, spec = _ab_plan()
        _write_failed_status(tmp_path, "a")

        # Running only B; A is omitted (failed)
        errors = _verify_ancestors(tmp_path, plan, {"b"}, spec, {})
        assert len(errors) == 1
        assert "a" in errors[0]
        assert "failed" in errors[0]

    def test_override_satisfies_failed_ancestor(self, tmp_path: Path) -> None:
        """After applying a global override on A, B's ancestor check passes."""
        plan, spec = _ab_plan()
        _write_failed_status(tmp_path, "a")

        upsert_override(
            tmp_path,
            step="a",
            by="levy",
            note="14/17 usable, deadline triage",
        )

        errors = _verify_ancestors(tmp_path, plan, {"b"}, spec, {})
        assert errors == []

    def test_scoped_override_for_other_step_does_not_satisfy(self, tmp_path: Path) -> None:
        """An override scoped to for_downstream=["other"] does NOT satisfy B."""
        plan, spec = _ab_plan()
        _write_failed_status(tmp_path, "a")

        upsert_override(
            tmp_path,
            step="a",
            for_downstream=["other-step"],
            by="levy",
            note="only for other-step",
        )

        errors = _verify_ancestors(tmp_path, plan, {"b"}, spec, {})
        assert len(errors) == 1
        assert "a" in errors[0]

    def test_scoped_override_for_downstream_b_satisfies(self, tmp_path: Path) -> None:
        """An override scoped to for_downstream=["b"] satisfies B."""
        plan, spec = _ab_plan()
        _write_failed_status(tmp_path, "a")

        upsert_override(
            tmp_path,
            step="a",
            for_downstream=["b"],
            by="levy",
            note="scoped to b only",
        )

        errors = _verify_ancestors(tmp_path, plan, {"b"}, spec, {})
        assert errors == []

    def test_no_override_file_still_blocks(self, tmp_path: Path) -> None:
        """When no overrides file exists, failed ancestor still blocks."""
        plan, spec = _ab_plan()
        _write_failed_status(tmp_path, "a")

        errors = _verify_ancestors(tmp_path, plan, {"b"}, spec, {})
        assert len(errors) == 1

    def test_three_step_chain_override_on_middle(self, tmp_path: Path) -> None:
        """A -> B -> C: override on B lets C proceed when B is failed."""
        plan = _make_plan(
            _step("a"),
            _step("b", ["a"]),
            _step("c", ["b"]),
        )
        spec = _make_spec("a", "b", "c")
        _write_failed_status(tmp_path, "b")

        a_state = tmp_path / STATE_DIR / TASKS_SUBDIR / "a"
        a_state.mkdir(parents=True, exist_ok=True)
        _ws(
            a_state,
            _SR(
                run_id="test/run1",
                step_id="a",
                item={"step": "a"},
                state="completed",
                started_at="2026-04-24T00:00:00",
                completed_at="2026-04-24T00:01:00",
            ),
        )

        # Without override on B, C is blocked
        errors = _verify_ancestors(tmp_path, plan, {"c"}, spec, {})
        assert len(errors) == 1
        assert "b" in errors[0]

        # With override on B, C proceeds
        upsert_override(tmp_path, step="b", by="levy", note="force b")
        errors = _verify_ancestors(tmp_path, plan, {"c"}, spec, {})
        assert errors == []
