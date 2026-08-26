"""Read-only task and accepted-output projection tests."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from metaproc.engine.dep_state import fingerprint_step
from metaproc.io import to_yaml_string
from metaproc.io.state_io import (
    end_attempt_at,
    start_attempt_at,
    write_result_at,
    write_status_at,
)
from metaproc.models.authored import IOSpec, ProcessSpec
from metaproc.models.plan import FanOut, Plan, ResolvedStep
from metaproc.models.plan_bundle import PlanBundle
from metaproc.models.runtime import AttemptDisposition, ResultRecord, StatusRecord
from metaproc.models.viz import TaskKeyProjection
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
                    fan_out=FanOut(over="items", bind="item", source="items.md"),
                ),
                ResolvedStep(step_id="scalar-child", mode="composite"),
                ResolvedStep(
                    step_id="mapped-child",
                    mode="composite",
                    fan_out=FanOut(over="items", bind="item", source="items.md"),
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
