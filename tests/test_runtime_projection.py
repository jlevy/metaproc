"""Read-only task and accepted-output projection tests."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from metaproc.engine.dep_state import fingerprint_step
from metaproc.io import to_yaml_string
from metaproc.io.state_io import (
    end_attempt_at,
    read_run_plan,
    start_attempt_at,
    write_result_at,
    write_status_at,
)
from metaproc.models.authored import IOSpec, ProcessSpec
from metaproc.models.plan import FanOut, Plan, ResolvedStep
from metaproc.models.plan_bundle import PlanBundle
from metaproc.models.runtime import AttemptDisposition, ResultRecord, StatusRecord
from metaproc.models.viz import TaskKeyProjection
from metaproc.paths import ATTEMPT_ANOMALIES_FILE, ATTEMPTS_SUBDIR
from metaproc.runtime_projection import scan_task_output_projection

ROOT_RUN_ID = "root/run-1"


def _bundle() -> PlanBundle:
    child = PlanBundle(
        plan=Plan(
            process="child.process.md",
            steps=[
                ResolvedStep(
                    step_id="leaf",
                    mode="code",
                    outputs={
                        "report": IOSpec(
                            path="{{run.dir}}/report.md",
                            format="frontmatter-md",
                            contract="example:Report/v1",
                        )
                    },
                )
            ],
        ),
        spec=ProcessSpec(name="child"),
        source_path="child.process.md",
    )
    return PlanBundle(
        plan=Plan(
            process="root.process.md",
            steps=[
                ResolvedStep(step_id="root-scalar", mode="code"),
                ResolvedStep(
                    step_id="root-map",
                    mode="code",
                    fan_out=FanOut(
                        over="items",
                        bind="item",
                        source="items.md",
                        items=[{"item": "AAA"}],
                    ),
                ),
                ResolvedStep(step_id="scalar-child", mode="composite"),
                ResolvedStep(
                    step_id="mapped-child",
                    mode="composite",
                    fan_out=FanOut(
                        over="items",
                        bind="item",
                        source="items.md",
                        items=[{"item": "BBB"}],
                    ),
                ),
            ],
        ),
        spec=ProcessSpec(name="root"),
        source_path="root.process.md",
        children={"scalar-child": child, "mapped-child": child},
    )


def _write_run_config(
    run_dir: Path,
    *,
    original_run_dir: Path,
    process: str = "root",
    run_context: str = "run-1",
) -> None:
    path = run_dir / ".state" / "run-config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        to_yaml_string(
            {
                "metaproc_layout": "v2",
                "process": process,
                "process_spec": "root.process.md",
                "run_id": run_context,
                "run_dir": str(original_run_dir),
                "variables": {},
                "backend": "local",
            }
        ),
        encoding="utf-8",
    )


def _write_run_plan(
    scope_dir: Path,
    *,
    run_id: str,
    scope_path: tuple[str, ...],
    plan: Plan,
    step_fingerprints: dict[str, str],
) -> None:
    path = scope_dir / ".state" / "run-plan.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        to_yaml_string(
            {
                "run_plan": {
                    "schema": "metaproc:RunPlanSnapshot/0.1",
                    "run_id": run_id,
                    "scope_path": list(scope_path),
                    "steps": [
                        {
                            "step_id": step.step_id,
                            "mode": step.mode,
                            "task_shape": ("mapped" if step.fan_out is not None else "scalar"),
                            "item_keys": (
                                [item[step.fan_out.bind] for item in step.fan_out.items]
                                if step.fan_out is not None
                                else []
                            ),
                            "outputs": {
                                name: declaration.model_dump(
                                    mode="json", by_alias=True, exclude_none=True
                                )
                                for name, declaration in step.outputs.items()
                            },
                            "fingerprint": step_fingerprints[step.step_id],
                        }
                        for step in plan.steps
                    ],
                }
            }
        ),
        encoding="utf-8",
    )


def _write_task(
    state_dir: Path,
    *,
    run_id: str,
    step_id: str,
    item_key: str | None = None,
    scope_path: tuple[str, ...] = (),
    state: Literal["completed", "failed"] = "completed",
    outputs: dict[str, str] | None = None,
    step_hash: str | None = None,
) -> str:
    item = {"item": item_key} if item_key is not None else {"step": step_id}
    attempt = start_attempt_at(
        state_dir,
        run_id=run_id,
        step_id=step_id,
        item=item,
        item_key=item_key,
        scope_path=scope_path,
    )
    disposition = (
        AttemptDisposition.succeeded if state == "completed" else AttemptDisposition.permanent
    )
    terminal = end_attempt_at(
        state_dir,
        attempt_id=attempt.attempt_id,
        disposition=disposition,
        failure_class=None if state == "completed" else "agent_error",
        error=None if state == "completed" else "failed",
    )
    write_status_at(
        state_dir,
        StatusRecord(
            run_id=run_id,
            step_id=step_id,
            item=item,
            state=state,
            attempt=terminal.attempt_number,
            attempt_id=terminal.attempt_id,
            generation=terminal.generation,
            fence_epoch=terminal.fence_epoch,
            started_at=terminal.started_at,
            completed_at=terminal.ended_at,
            failure_class=terminal.failure_class,
            error=terminal.error,
        ),
    )
    if outputs is not None:
        write_result_at(
            state_dir,
            ResultRecord(
                run_id=run_id,
                step_id=step_id,
                state="completed",
                validated=True,
                outputs=outputs,
                attempt_id=terminal.attempt_id,
                step_hash=step_hash,
            ),
        )
    return terminal.attempt_id


def _leaf_step() -> ResolvedStep:
    return _bundle().children["scalar-child"].plan.steps[0]


def test_projection_indexes_scalar_mapped_and_recursive_scopes(tmp_path: Path) -> None:
    run_dir = tmp_path / "hydrated" / "run-1"
    _write_run_config(run_dir, original_run_dir=run_dir)
    _write_task(run_dir / ".state/tasks/root-scalar", run_id=ROOT_RUN_ID, step_id="root-scalar")
    _write_task(
        run_dir / ".state/tasks/root-map/AAA",
        run_id=ROOT_RUN_ID,
        step_id="root-map",
        item_key="AAA",
    )
    _write_task(
        run_dir / "scalar-child/.state/tasks/leaf",
        run_id=f"{ROOT_RUN_ID}/scalar-child",
        step_id="leaf",
        scope_path=("scalar-child",),
    )
    _write_task(
        run_dir / "mapped-child/BBB/.state/tasks/leaf",
        run_id=f"{ROOT_RUN_ID}/mapped-child/BBB",
        step_id="leaf",
        scope_path=("mapped-child", "BBB"),
    )

    projection = scan_task_output_projection(run_dir, _bundle())

    assert [task.key for task in projection.tasks] == [
        TaskKeyProjection(step_id="root-map", item_key="AAA"),
        TaskKeyProjection(step_id="root-scalar"),
        TaskKeyProjection(step_id="leaf", scope_path=["mapped-child", "BBB"]),
        TaskKeyProjection(step_id="leaf", scope_path=["scalar-child"]),
    ]
    assert all(task.attempt_id is not None for task in projection.tasks)


def test_projection_uses_complete_snapshots_without_authored_bundle(tmp_path: Path) -> None:
    original_run_dir = Path("/mnt/shared/runs/run-1")
    run_dir = tmp_path / "hydrated" / "run-1"
    artifact = run_dir / "scalar-child" / "report.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("report", encoding="utf-8")
    _write_run_config(run_dir, original_run_dir=original_run_dir)

    bundle = _bundle()
    _write_run_plan(
        run_dir,
        run_id=ROOT_RUN_ID,
        scope_path=(),
        plan=bundle.plan,
        step_fingerprints={step.step_id: fingerprint_step(step) for step in bundle.plan.steps},
    )
    for scope_path, child_name in (
        (("scalar-child",), "scalar-child"),
        (("mapped-child", "BBB"), "mapped-child"),
    ):
        child = bundle.children[child_name]
        _write_run_plan(
            run_dir.joinpath(*scope_path),
            run_id="/".join((ROOT_RUN_ID, *scope_path)),
            scope_path=scope_path,
            plan=child.plan,
            step_fingerprints={step.step_id: fingerprint_step(step) for step in child.plan.steps},
        )

    _write_task(run_dir / ".state/tasks/root-scalar", run_id=ROOT_RUN_ID, step_id="root-scalar")
    _write_task(
        run_dir / ".state/tasks/root-map/AAA",
        run_id=ROOT_RUN_ID,
        step_id="root-map",
        item_key="AAA",
    )
    _write_task(
        run_dir / ".state/tasks/mapped-child/BBB",
        run_id=ROOT_RUN_ID,
        step_id="mapped-child",
        item_key="BBB",
    )
    _write_task(
        run_dir / "scalar-child/.state/tasks/leaf",
        run_id=f"{ROOT_RUN_ID}/scalar-child",
        step_id="leaf",
        scope_path=("scalar-child",),
        outputs={"report": "/mnt/shared/runs/run-1/scalar-child/report.md"},
        step_hash=fingerprint_step(_leaf_step()),
    )
    _write_task(
        run_dir / "mapped-child/BBB/.state/tasks/leaf",
        run_id=f"{ROOT_RUN_ID}/mapped-child/BBB",
        step_id="leaf",
        scope_path=("mapped-child", "BBB"),
    )

    projection = scan_task_output_projection(run_dir)

    assert [task.key for task in projection.tasks] == [
        TaskKeyProjection(step_id="mapped-child", item_key="BBB"),
        TaskKeyProjection(step_id="root-map", item_key="AAA"),
        TaskKeyProjection(step_id="root-scalar"),
        TaskKeyProjection(step_id="leaf", scope_path=["mapped-child", "BBB"]),
        TaskKeyProjection(step_id="leaf", scope_path=["scalar-child"]),
    ]
    assert projection.coverage_gaps == []
    scalar_child = projection.tasks[-1]
    assert [output.path for output in scalar_child.accepted_outputs] == [str(artifact)]
    assert scalar_child.unaccepted_outputs == []


def test_projection_reports_missing_declared_scalar_state(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    _write_run_config(run_dir, original_run_dir=run_dir)
    plan = Plan(
        process="root.process.md",
        steps=[ResolvedStep(step_id="missing-scalar", mode="code")],
    )
    _write_run_plan(
        run_dir,
        run_id=ROOT_RUN_ID,
        scope_path=(),
        plan=plan,
        step_fingerprints={"missing-scalar": fingerprint_step(plan.steps[0])},
    )

    projection = scan_task_output_projection(run_dir)

    assert projection.tasks == []
    assert [gap.model_dump() for gap in projection.coverage_gaps] == [
        {
            "key": {"step_id": "missing-scalar", "item_key": None, "scope_path": []},
            "reason": "task-state-missing",
        }
    ]


def test_projection_reports_missing_declared_mapped_item_state(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    _write_run_config(run_dir, original_run_dir=run_dir)
    plan = Plan(
        process="root.process.md",
        steps=[
            ResolvedStep(
                step_id="mapped",
                mode="code",
                fan_out=FanOut(
                    over="items",
                    bind="item",
                    source="items.md",
                    items=[{"item": "AAA"}, {"item": "BBB"}],
                ),
            )
        ],
    )
    _write_run_plan(
        run_dir,
        run_id=ROOT_RUN_ID,
        scope_path=(),
        plan=plan,
        step_fingerprints={"mapped": fingerprint_step(plan.steps[0])},
    )
    _write_task(
        run_dir / ".state/tasks/mapped/AAA",
        run_id=ROOT_RUN_ID,
        step_id="mapped",
        item_key="AAA",
    )

    projection = scan_task_output_projection(run_dir)

    assert [task.key for task in projection.tasks] == [
        TaskKeyProjection(step_id="mapped", item_key="AAA")
    ]
    assert [gap.model_dump() for gap in projection.coverage_gaps] == [
        {
            "key": {"step_id": "mapped", "item_key": "BBB", "scope_path": []},
            "reason": "task-state-missing",
        }
    ]


def test_projection_rejects_declared_task_state_alias(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    _write_run_config(run_dir, original_run_dir=run_dir)
    plan = Plan(
        process="root.process.md",
        steps=[
            ResolvedStep(
                step_id="mapped",
                mode="code",
                fan_out=FanOut(
                    over="items",
                    bind="item",
                    source="items.md",
                    items=[{"item": "AAA"}, {"item": "BBB"}],
                ),
            )
        ],
    )
    _write_run_plan(
        run_dir,
        run_id=ROOT_RUN_ID,
        scope_path=(),
        plan=plan,
        step_fingerprints={"mapped": fingerprint_step(plan.steps[0])},
    )
    state_root = run_dir / ".state/tasks/mapped"
    _write_task(
        state_root / "AAA",
        run_id=ROOT_RUN_ID,
        step_id="mapped",
        item_key="AAA",
    )
    (state_root / "BBB").symlink_to(state_root / "AAA", target_is_directory=True)

    with pytest.raises(ValueError, match="does not match expected task fields item_key"):
        scan_task_output_projection(run_dir)


def test_projection_reports_missing_declared_composite_scope_state(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    _write_run_config(run_dir, original_run_dir=run_dir)
    plan = Plan(
        process="root.process.md",
        steps=[ResolvedStep(step_id="child", mode="composite")],
    )
    _write_run_plan(
        run_dir,
        run_id=ROOT_RUN_ID,
        scope_path=(),
        plan=plan,
        step_fingerprints={"child": fingerprint_step(plan.steps[0])},
    )
    projection = scan_task_output_projection(run_dir)

    assert projection.tasks == []
    assert [gap.model_dump() for gap in projection.coverage_gaps] == [
        {
            "key": {"step_id": "child", "item_key": None, "scope_path": []},
            "reason": "scope-state-missing",
        }
    ]


def test_projection_uses_child_scope_as_scalar_composite_state(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    child_dir = run_dir / "scalar-child"
    _write_run_config(run_dir, original_run_dir=run_dir)
    root_plan = Plan(
        process="root.process.md",
        steps=[ResolvedStep(step_id="scalar-child", mode="composite")],
    )
    child_plan = _bundle().children["scalar-child"].plan
    _write_run_plan(
        run_dir,
        run_id=ROOT_RUN_ID,
        scope_path=(),
        plan=root_plan,
        step_fingerprints={"scalar-child": fingerprint_step(root_plan.steps[0])},
    )
    _write_run_plan(
        child_dir,
        run_id=f"{ROOT_RUN_ID}/scalar-child",
        scope_path=("scalar-child",),
        plan=child_plan,
        step_fingerprints={"leaf": fingerprint_step(child_plan.steps[0])},
    )
    _write_task(
        child_dir / ".state/tasks/leaf",
        run_id=f"{ROOT_RUN_ID}/scalar-child",
        step_id="leaf",
        scope_path=("scalar-child",),
    )

    projection = scan_task_output_projection(run_dir)

    assert [task.key for task in projection.tasks] == [
        TaskKeyProjection(step_id="leaf", scope_path=["scalar-child"])
    ]
    assert projection.coverage_gaps == []


def test_projection_without_bundle_requires_root_snapshot(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    _write_run_config(run_dir, original_run_dir=run_dir)

    with pytest.raises(ValueError, match="run-plan.yaml.*required without a plan bundle"):
        scan_task_output_projection(run_dir)


def test_projection_without_bundle_requires_declared_child_snapshot(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    child_dir = run_dir / "scalar-child"
    _write_run_config(run_dir, original_run_dir=run_dir)
    root_plan = Plan(
        process="root.process.md",
        steps=[ResolvedStep(step_id="scalar-child", mode="composite")],
    )
    _write_run_plan(
        run_dir,
        run_id=ROOT_RUN_ID,
        scope_path=(),
        plan=root_plan,
        step_fingerprints={"scalar-child": fingerprint_step(root_plan.steps[0])},
    )
    _write_task(
        child_dir / ".state/tasks/leaf",
        run_id=f"{ROOT_RUN_ID}/scalar-child",
        step_id="leaf",
        scope_path=("scalar-child",),
    )

    with pytest.raises(ValueError, match="scalar-child.*run-plan.yaml.*required"):
        scan_task_output_projection(run_dir)


def test_projection_uses_real_run_process_identity_shape(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    _write_run_config(run_dir, original_run_dir=run_dir, run_context="r1")
    _write_task(
        run_dir / ".state/tasks/root-scalar",
        run_id="root/r1",
        step_id="root-scalar",
    )

    assert scan_task_output_projection(run_dir, _bundle()).tasks[0].state == "completed"


def test_projection_rejects_plan_for_a_different_process(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    _write_run_config(run_dir, original_run_dir=run_dir, process="another-process")

    with pytest.raises(ValueError, match="does not match loaded plan"):
        scan_task_output_projection(run_dir, _bundle())


def test_failed_task_without_result_remains_visible(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    _write_run_config(run_dir, original_run_dir=run_dir)
    _write_task(
        run_dir / ".state/tasks/root-scalar",
        run_id=ROOT_RUN_ID,
        step_id="root-scalar",
        state="failed",
    )

    task = scan_task_output_projection(run_dir, _bundle()).tasks[0]

    assert task.state == "failed"
    assert task.result_binding == "none"
    assert task.accepted_outputs == []
    assert task.unaccepted_outputs == []


def test_accepted_output_requires_exact_bindings_and_available_artifact(tmp_path: Path) -> None:
    original_run_dir = Path("/mnt/filestore/runs/run-1")
    run_dir = tmp_path / "hydrated" / "run-1"
    artifact = run_dir / "scalar-child" / "report.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("report", encoding="utf-8")
    _write_run_config(run_dir, original_run_dir=original_run_dir)
    _write_task(
        run_dir / "scalar-child/.state/tasks/leaf",
        run_id=f"{ROOT_RUN_ID}/scalar-child",
        step_id="leaf",
        scope_path=("scalar-child",),
        outputs={"report": "/mnt/filestore/runs/run-1/scalar-child/report.md"},
        step_hash=fingerprint_step(_leaf_step()),
    )

    task = scan_task_output_projection(run_dir, _bundle()).tasks[0]
    accepted = task.accepted_outputs[0]

    assert task.result_binding == "exact"
    assert task.step_binding == "exact"
    assert accepted.name == "report"
    assert accepted.path == str(artifact)
    assert accepted.recorded_path == "/mnt/filestore/runs/run-1/scalar-child/report.md"
    assert accepted.declaration == IOSpec(
        path="{{run.dir}}/report.md",
        format="frontmatter-md",
        contract="example:Report/v1",
    )
    assert task.unaccepted_outputs == []


def test_scope_run_plan_binds_outputs_without_rebuilding_the_executed_step(tmp_path: Path) -> None:
    run_dir = tmp_path / "hydrated" / "run-1"
    scope_dir = run_dir / "scalar-child"
    artifact = scope_dir / "report.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("report", encoding="utf-8")
    _write_run_config(run_dir, original_run_dir=run_dir)
    _write_run_plan(
        scope_dir,
        run_id=f"{ROOT_RUN_ID}/scalar-child",
        scope_path=("scalar-child",),
        plan=_bundle().children["scalar-child"].plan,
        step_fingerprints={"leaf": "0123456789abcdef"},
    )
    _write_task(
        scope_dir / ".state/tasks/leaf",
        run_id=f"{ROOT_RUN_ID}/scalar-child",
        step_id="leaf",
        scope_path=("scalar-child",),
        outputs={"report": str(artifact)},
        step_hash="0123456789abcdef",
    )

    task = scan_task_output_projection(run_dir, _bundle()).tasks[0]

    assert task.step_binding == "exact"
    assert [output.name for output in task.accepted_outputs] == ["report"]
    assert task.unaccepted_outputs == []


def test_scope_run_plan_must_match_runtime_scope_identity(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    scope_dir = run_dir / "scalar-child"
    _write_run_config(run_dir, original_run_dir=run_dir)
    _write_run_plan(
        scope_dir,
        run_id=f"{ROOT_RUN_ID}/scalar-child",
        scope_path=("another-child",),
        plan=_bundle().children["scalar-child"].plan,
        step_fingerprints={"leaf": fingerprint_step(_leaf_step())},
    )

    with pytest.raises(ValueError, match="scope path .* does not match runtime path"):
        scan_task_output_projection(run_dir, _bundle())


def test_scope_run_plan_must_match_runtime_run_identity(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    scope_dir = run_dir / "scalar-child"
    _write_run_config(run_dir, original_run_dir=run_dir)
    _write_run_plan(
        scope_dir,
        run_id="another/run/scalar-child",
        scope_path=("scalar-child",),
        plan=_bundle().children["scalar-child"].plan,
        step_fingerprints={"leaf": fingerprint_step(_leaf_step())},
    )

    with pytest.raises(ValueError, match="run id .* does not match runtime identity"):
        scan_task_output_projection(run_dir, _bundle())


def test_run_plan_snapshot_rejects_unknown_schema_version(tmp_path: Path) -> None:
    plan = _bundle().plan
    _write_run_plan(
        tmp_path,
        run_id=ROOT_RUN_ID,
        scope_path=(),
        plan=plan,
        step_fingerprints={step.step_id: fingerprint_step(step) for step in plan.steps},
    )
    path = tmp_path / ".state/run-plan.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("RunPlanSnapshot/0.1", "RunPlanSnapshot/9.9"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="RunPlanSnapshot/0.1"):
        read_run_plan(tmp_path)


def test_scope_run_plan_cannot_authorize_undeclared_child(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    ghost_dir = run_dir / "ghost"
    _write_run_config(run_dir, original_run_dir=run_dir)
    _write_run_plan(
        ghost_dir,
        run_id=f"{ROOT_RUN_ID}/ghost",
        scope_path=("ghost",),
        plan=_bundle().children["scalar-child"].plan,
        step_fingerprints={"leaf": fingerprint_step(_leaf_step())},
    )
    _write_task(
        ghost_dir / ".state/tasks/leaf",
        run_id=f"{ROOT_RUN_ID}/ghost",
        step_id="leaf",
        scope_path=("ghost",),
    )

    assert scan_task_output_projection(run_dir, _bundle()).tasks == []


def test_parent_run_plan_can_remove_a_stale_child_scope(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    child_dir = run_dir / "scalar-child"
    _write_run_config(run_dir, original_run_dir=run_dir)
    root_plan = Plan(
        process="root.process.md",
        steps=[ResolvedStep(step_id="root-scalar", mode="code")],
    )
    _write_run_plan(
        run_dir,
        run_id=ROOT_RUN_ID,
        scope_path=(),
        plan=root_plan,
        step_fingerprints={"root-scalar": fingerprint_step(root_plan.steps[0])},
    )
    _write_run_plan(
        child_dir,
        run_id=f"{ROOT_RUN_ID}/scalar-child",
        scope_path=("scalar-child",),
        plan=_bundle().children["scalar-child"].plan,
        step_fingerprints={"leaf": fingerprint_step(_leaf_step())},
    )
    _write_task(
        child_dir / ".state/tasks/leaf",
        run_id=f"{ROOT_RUN_ID}/scalar-child",
        step_id="leaf",
        scope_path=("scalar-child",),
    )

    assert scan_task_output_projection(run_dir, _bundle()).tasks == []


def test_parent_run_plan_rejects_scalar_mapped_scope_shape_drift(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    child_dir = run_dir / "mapped-child" / "AAA"
    _write_run_config(run_dir, original_run_dir=run_dir)
    root_plan = Plan(
        process="root.process.md",
        steps=[ResolvedStep(step_id="mapped-child", mode="composite")],
    )
    _write_run_plan(
        run_dir,
        run_id=ROOT_RUN_ID,
        scope_path=(),
        plan=root_plan,
        step_fingerprints={"mapped-child": fingerprint_step(root_plan.steps[0])},
    )
    _write_run_plan(
        child_dir,
        run_id=f"{ROOT_RUN_ID}/mapped-child/AAA",
        scope_path=("mapped-child", "AAA"),
        plan=_bundle().children["mapped-child"].plan,
        step_fingerprints={"leaf": fingerprint_step(_leaf_step())},
    )
    _write_task(
        child_dir / ".state/tasks/leaf",
        run_id=f"{ROOT_RUN_ID}/mapped-child/AAA",
        step_id="leaf",
        scope_path=("mapped-child", "AAA"),
    )

    assert scan_task_output_projection(run_dir, _bundle()).tasks == []


def test_parent_run_plan_rejects_removed_mapped_child_item(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    ghost_dir = run_dir / "mapped-child" / "GHOST"
    _write_run_config(run_dir, original_run_dir=run_dir)
    root_plan = _bundle().plan
    _write_run_plan(
        run_dir,
        run_id=ROOT_RUN_ID,
        scope_path=(),
        plan=root_plan,
        step_fingerprints={step.step_id: fingerprint_step(step) for step in root_plan.steps},
    )
    _write_run_plan(
        ghost_dir,
        run_id=f"{ROOT_RUN_ID}/mapped-child/GHOST",
        scope_path=("mapped-child", "GHOST"),
        plan=_bundle().children["mapped-child"].plan,
        step_fingerprints={"leaf": fingerprint_step(_leaf_step())},
    )
    _write_task(
        ghost_dir / ".state/tasks/leaf",
        run_id=f"{ROOT_RUN_ID}/mapped-child/GHOST",
        step_id="leaf",
        scope_path=("mapped-child", "GHOST"),
    )

    assert scan_task_output_projection(run_dir, _bundle()).tasks == []


def test_scope_run_plan_rejects_removed_mapped_task_item(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    _write_run_config(run_dir, original_run_dir=run_dir)
    root_plan = _bundle().plan
    _write_run_plan(
        run_dir,
        run_id=ROOT_RUN_ID,
        scope_path=(),
        plan=root_plan,
        step_fingerprints={step.step_id: fingerprint_step(step) for step in root_plan.steps},
    )
    _write_task(
        run_dir / ".state/tasks/root-map/GHOST",
        run_id=ROOT_RUN_ID,
        step_id="root-map",
        item_key="GHOST",
    )

    assert scan_task_output_projection(run_dir, _bundle()).tasks == []


def test_stale_prior_result_is_visible_but_not_accepted_for_latest_attempt(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    artifact = run_dir / "scalar-child" / "report.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("attempt one", encoding="utf-8")
    _write_run_config(run_dir, original_run_dir=run_dir)
    state_dir = run_dir / "scalar-child/.state/tasks/leaf"
    first_attempt_id = _write_task(
        state_dir,
        run_id=f"{ROOT_RUN_ID}/scalar-child",
        step_id="leaf",
        scope_path=("scalar-child",),
        outputs={"report": str(artifact)},
        step_hash=fingerprint_step(_leaf_step()),
    )
    second = start_attempt_at(
        state_dir,
        run_id=f"{ROOT_RUN_ID}/scalar-child",
        step_id="leaf",
        item={"step": "leaf"},
        scope_path=("scalar-child",),
    )
    terminal = end_attempt_at(
        state_dir,
        attempt_id=second.attempt_id,
        disposition=AttemptDisposition.succeeded,
    )
    write_status_at(
        state_dir,
        StatusRecord(
            run_id=f"{ROOT_RUN_ID}/scalar-child",
            step_id="leaf",
            item={"step": "leaf"},
            state="completed",
            attempt=terminal.attempt_number,
            attempt_id=terminal.attempt_id,
            generation=terminal.generation,
            fence_epoch=terminal.fence_epoch,
            started_at=terminal.started_at,
            completed_at=terminal.ended_at,
        ),
    )

    task = scan_task_output_projection(run_dir, _bundle()).tasks[0]

    assert first_attempt_id != terminal.attempt_id
    assert task.result_binding == "attempt-mismatch"
    assert task.accepted_outputs == []
    assert [output.reason for output in task.unaccepted_outputs] == ["attempt-mismatch"]


@pytest.mark.parametrize(
    ("step_hash", "output_name", "expected_reason"),
    [
        ("stale-step-hash", "report", "step-mismatch"),
        (None, "report", "legacy-unbound-step"),
        ("CURRENT", "unknown", "undeclared"),
    ],
)
def test_projection_rejects_stale_legacy_and_undeclared_outputs(
    tmp_path: Path,
    step_hash: str | None,
    output_name: str,
    expected_reason: str,
) -> None:
    run_dir = tmp_path / "run-1"
    artifact = run_dir / "scalar-child" / "report.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("report", encoding="utf-8")
    _write_run_config(run_dir, original_run_dir=run_dir)
    effective_hash = fingerprint_step(_leaf_step()) if step_hash == "CURRENT" else step_hash
    _write_task(
        run_dir / "scalar-child/.state/tasks/leaf",
        run_id=f"{ROOT_RUN_ID}/scalar-child",
        step_id="leaf",
        scope_path=("scalar-child",),
        outputs={output_name: str(artifact)},
        step_hash=effective_hash,
    )

    task = scan_task_output_projection(run_dir, _bundle()).tasks[0]

    assert task.accepted_outputs == []
    assert [output.reason for output in task.unaccepted_outputs] == [expected_reason]


@pytest.mark.parametrize(
    ("artifact_setup", "recorded_path", "expected_reason"),
    [
        ("missing", "inside", "missing"),
        ("directory", "inside", "kind-mismatch"),
        ("file", "external", "external"),
        ("symlink", "inside", "external"),
    ],
)
def test_unavailable_or_nonportable_output_is_diagnostic_not_accepted(
    tmp_path: Path,
    artifact_setup: str,
    recorded_path: str,
    expected_reason: str,
) -> None:
    original_run_dir = Path("/mnt/filestore/runs/run-1")
    run_dir = tmp_path / "hydrated" / "run-1"
    artifact = run_dir / "scalar-child" / "report.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    if artifact_setup == "file":
        artifact.write_text("report", encoding="utf-8")
    elif artifact_setup == "directory":
        artifact.mkdir()
    elif artifact_setup == "symlink":
        outside = tmp_path / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        artifact.symlink_to(outside)
    _write_run_config(run_dir, original_run_dir=original_run_dir)
    recorded = (
        "/var/lib/external/report.md"
        if recorded_path == "external"
        else "/mnt/filestore/runs/run-1/scalar-child/report.md"
    )
    _write_task(
        run_dir / "scalar-child/.state/tasks/leaf",
        run_id=f"{ROOT_RUN_ID}/scalar-child",
        step_id="leaf",
        scope_path=("scalar-child",),
        outputs={"report": recorded},
        step_hash=fingerprint_step(_leaf_step()),
    )

    task = scan_task_output_projection(run_dir, _bundle()).tasks[0]

    assert task.accepted_outputs == []
    assert [output.reason for output in task.unaccepted_outputs] == [expected_reason]


def test_projection_uses_total_order_for_scalar_and_mapped_same_step_id(tmp_path: Path) -> None:
    child = PlanBundle(
        plan=Plan(
            process="child.process.md",
            steps=[
                ResolvedStep(
                    step_id="same",
                    mode="code",
                    fan_out=FanOut(over="items", bind="item", source="items.md"),
                )
            ],
        ),
        spec=ProcessSpec(name="child"),
        source_path="child.process.md",
    )
    bundle = PlanBundle(
        plan=Plan(
            process="root.process.md",
            steps=[
                ResolvedStep(step_id="same", mode="code"),
                ResolvedStep(step_id="child", mode="composite"),
            ],
        ),
        spec=ProcessSpec(name="root"),
        source_path="root.process.md",
        children={"child": child},
    )
    run_dir = tmp_path / "run-1"
    _write_run_config(run_dir, original_run_dir=run_dir)
    _write_task(run_dir / ".state/tasks/same", run_id=ROOT_RUN_ID, step_id="same")
    _write_task(
        run_dir / "child/.state/tasks/same/AAA",
        run_id=f"{ROOT_RUN_ID}/child",
        step_id="same",
        item_key="AAA",
        scope_path=("child",),
    )

    tasks = scan_task_output_projection(run_dir, bundle).tasks

    assert [task.key for task in tasks] == [
        TaskKeyProjection(step_id="same"),
        TaskKeyProjection(step_id="same", item_key="AAA", scope_path=["child"]),
    ]


def test_projection_ignores_symlinked_scope_and_task_state_escapes(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    _write_run_config(run_dir, original_run_dir=run_dir)
    outside_scope = tmp_path / "outside-scope"
    _write_task(
        outside_scope / ".state/tasks/leaf",
        run_id=f"{ROOT_RUN_ID}/scalar-child",
        step_id="leaf",
        scope_path=("scalar-child",),
    )
    (run_dir / "scalar-child").symlink_to(outside_scope, target_is_directory=True)

    outside_item = tmp_path / "outside-item"
    _write_task(outside_item, run_id=ROOT_RUN_ID, step_id="root-map", item_key="AAA")
    mapped_root = run_dir / ".state/tasks/root-map"
    mapped_root.mkdir(parents=True)
    (mapped_root / "AAA").symlink_to(outside_item, target_is_directory=True)

    assert scan_task_output_projection(run_dir, _bundle()).tasks == []


def test_projection_rejects_symlinked_state_record_escape(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    _write_run_config(run_dir, original_run_dir=run_dir)
    state_dir = run_dir / ".state/tasks/root-scalar"
    outside = tmp_path / "outside-status.yaml"
    outside.write_text(
        to_yaml_string(
            StatusRecord(
                run_id=ROOT_RUN_ID,
                step_id="root-scalar",
                item={"step": "root-scalar"},
                state="failed",
            ).model_dump(mode="json")
        ),
        encoding="utf-8",
    )
    state_dir.mkdir(parents=True)
    (state_dir / "status.yaml").symlink_to(outside)

    with pytest.raises(ValueError, match="runtime state path .* escapes run tree"):
        scan_task_output_projection(run_dir, _bundle())


def test_projection_rejects_symlinked_attempt_anomaly_escape(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    _write_run_config(run_dir, original_run_dir=run_dir)
    state_dir = run_dir / ".state/tasks/root-scalar"
    attempt_id = _write_task(state_dir, run_id=ROOT_RUN_ID, step_id="root-scalar")
    outside = tmp_path / ATTEMPT_ANOMALIES_FILE
    outside.write_text("outside", encoding="utf-8")
    (state_dir / ATTEMPTS_SUBDIR / attempt_id / ATTEMPT_ANOMALIES_FILE).symlink_to(outside)

    with pytest.raises(ValueError, match="runtime state path .* escapes run tree"):
        scan_task_output_projection(run_dir, _bundle())


def test_projection_rejects_symlinked_run_plan_escape(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    scope_dir = run_dir / "scalar-child"
    _write_run_config(run_dir, original_run_dir=run_dir)
    outside_scope = tmp_path / "outside-scope"
    _write_run_plan(
        outside_scope,
        run_id=f"{ROOT_RUN_ID}/scalar-child",
        scope_path=("scalar-child",),
        plan=_bundle().children["scalar-child"].plan,
        step_fingerprints={"leaf": fingerprint_step(_leaf_step())},
    )
    state_dir = scope_dir / ".state"
    state_dir.mkdir(parents=True)
    (state_dir / "run-plan.yaml").symlink_to(outside_scope / ".state/run-plan.yaml")

    with pytest.raises(ValueError, match="runtime state path .* escapes run tree"):
        scan_task_output_projection(run_dir, _bundle())


def test_a_produced_runbook_step_keeps_its_outputs_accepted(tmp_path: Path) -> None:
    """Regression for the plan-time/execution-time fingerprint split.

    ``_publish_run_plan`` writes the run plan at launch, when a runbook an earlier
    step produces does not exist yet; the result record is written at execution
    time, when it does. If those two hashes disagree, a completed and validated
    step is projected as ``step-mismatch`` and every one of its outputs is
    rejected — silently, for the entire class of step that loads a generated
    runbook. Nothing above this level catches it: the fixtures elsewhere in this
    file seed the snapshot with a hash taken at the same moment as the result.
    """
    run_dir = tmp_path / "hydrated" / "run-1"
    runbook = tmp_path / "written-by-an-earlier-step.md"

    step = ResolvedStep(
        step_id="consumer",
        mode="agent",
        prompt_paths=[str(runbook)],
        produced_refs=[str(runbook)],
        outputs={"report": IOSpec(path="{{run.dir}}/report.md")},
    )
    plan = Plan(process="root.process.md", steps=[step])
    bundle = PlanBundle(plan=plan, spec=ProcessSpec(name="root"), source_path="root.process.md")

    # Launch: the producing step has not run, so the runbook is absent.
    assert not runbook.exists()
    _write_run_config(run_dir, original_run_dir=run_dir)
    _write_run_plan(
        run_dir,
        run_id=ROOT_RUN_ID,
        scope_path=(),
        plan=plan,
        step_fingerprints={"consumer": fingerprint_step(step)},
    )

    # The earlier step runs and writes the runbook; the consumer then executes.
    runbook.write_text("# Runbook produced by an earlier step\n")
    report = run_dir / "report.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("# Report\n")
    _write_task(
        run_dir / ".state/tasks/consumer",
        run_id=ROOT_RUN_ID,
        step_id="consumer",
        outputs={"report": str(report)},
        step_hash=fingerprint_step(step),
    )

    task = scan_task_output_projection(run_dir, bundle).tasks[0]

    assert task.state == "completed"
    assert task.step_binding == "exact", (
        f"outputs rejected as {[(o.name, o.reason) for o in task.unaccepted_outputs]}"
    )
    assert [output.name for output in task.accepted_outputs] == ["report"]
