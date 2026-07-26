"""Tests for dep-state reporting and the ``metaproc deps`` command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.engine.dep_state import (
    build_dep_report,
    compute_step_state,
    fingerprint_step,
    recorded_step_hash,
)
from metaproc.io.state_io import (
    mark_completed_at,
    mark_running_at,
    write_attempt_at,
    write_result_at,
    write_status_at,
)
from metaproc.models.authored import IOSpec, StepStatus
from metaproc.models.plan import Plan, ResolvedAdapter, ResolvedDep, ResolvedStep
from metaproc.models.runtime import AttemptRecord, ResultRecord, StatusRecord, StepState
from metaproc.paths import STATE_DIR, STATUS_FILE, TASKS_SUBDIR


def _task_state_dir(run_dir: Path, step_id: str) -> Path:
    return run_dir / STATE_DIR / TASKS_SUBDIR / step_id


runner = CliRunner()


def _step(step_id: str, *, needs: list[str] | None = None) -> ResolvedStep:
    return ResolvedStep(
        step_id=step_id,
        mode="code",
        adapter=ResolvedAdapter(type="test", config={}),
        outputs={"out": IOSpec(path=f"runs/demo/{step_id}.md")},
        needs=needs or [],
    )


class TestBuildDepReport:
    def test_infers_all_runtime_states(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "runs" / "demo"
        run_dir.mkdir(parents=True)

        source_dep = tmp_path / "template.md"
        source_dep.write_text("template")

        present_step = _step("present-step")
        stale_step = _step("stale-step")
        inflight_step = _step("inflight-step")
        future_step = _step("future-step")

        present_state = _task_state_dir(run_dir, "present-step")
        present_state.mkdir(parents=True, exist_ok=True)
        present_running = mark_running_at(
            present_state,
            run_id="demo/run-1",
            step_id=present_step.step_id,
            item={"step": present_step.step_id},
        )
        mark_completed_at(present_state, running_record=present_running)
        present_path = run_dir / "present.md"
        present_path.write_text("present")
        write_result_at(
            present_state,
            ResultRecord(
                run_id="demo/run-1",
                step_id=present_step.step_id,
                state="completed",
                validated=True,
                outputs={"out": str(present_path)},
                published_at="2026-04-17T00:00:00",
                step_hash=fingerprint_step(present_step),
            ),
        )

        stale_state = _task_state_dir(run_dir, "stale-step")
        stale_state.mkdir(parents=True, exist_ok=True)
        stale_running = mark_running_at(
            stale_state,
            run_id="demo/run-1",
            step_id=stale_step.step_id,
            item={"step": stale_step.step_id},
        )
        mark_completed_at(stale_state, running_record=stale_running)
        stale_path = run_dir / "stale.md"
        stale_path.write_text("stale")
        write_result_at(
            stale_state,
            ResultRecord(
                run_id="demo/run-1",
                step_id=stale_step.step_id,
                state="completed",
                validated=True,
                outputs={"out": str(stale_path)},
                published_at="2026-04-17T00:00:01",
                step_hash="old-hash",
            ),
        )

        inflight_state = _task_state_dir(run_dir, "inflight-step")
        inflight_state.mkdir(parents=True, exist_ok=True)
        mark_running_at(
            inflight_state,
            run_id="demo/run-1",
            step_id=inflight_step.step_id,
            item={"step": inflight_step.step_id},
        )
        write_attempt_at(
            inflight_state,
            AttemptRecord(
                run_id="demo/run-1",
                step_id=inflight_step.step_id,
                item={"step": inflight_step.step_id},
                step_hash=fingerprint_step(inflight_step),
            ),
        )

        plan = Plan(
            process="demo",
            params={"RUN_ID": "run-1"},
            deps={
                "template": ResolvedDep(path=str(source_dep)),
                "present": ResolvedDep(
                    path=str(present_path),
                    produced_by="present-step.out",
                ),
                "stale": ResolvedDep(
                    path=str(stale_path),
                    produced_by="stale-step.out",
                ),
                "inflight": ResolvedDep(
                    path=str(run_dir / "inflight.md"),
                    produced_by="inflight-step.out",
                ),
                "supplied": ResolvedDep(path=str(run_dir / "supplied.md")),
                "future": ResolvedDep(
                    path=str(run_dir / "future.md"),
                    produced_by="future-step.out",
                ),
            },
            steps=[present_step, stale_step, inflight_step, future_step],
        )

        report = build_dep_report(plan, run_dir)

        assert report["template"].state == "present"
        assert report["present"].state == "present"
        assert report["stale"].state == "stale"
        assert report["inflight"].state == "in-flight"
        assert report["supplied"].state == "missing"
        assert report["future"].state == "not-yet-due"


class TestDepsCommand:
    def test_text_output_reports_dep_states(self, tmp_path: Path) -> None:
        process_dir = tmp_path / "proc"
        process_dir.mkdir()
        run_dir = tmp_path / "runs" / "demo"
        run_dir.mkdir(parents=True)

        template = process_dir / "template.md"
        template.write_text("template")
        items_path = run_dir / "items.md"
        items_path.write_text("items")

        setup_state = _task_state_dir(run_dir, "setup")
        setup_state.mkdir(parents=True, exist_ok=True)
        running = mark_running_at(
            setup_state, run_id="demo/demo", step_id="setup", item={"step": "setup"}
        )
        mark_completed_at(setup_state, running_record=running)

        (process_dir / "test.process.md").write_text(
            "\n".join(
                [
                    "---",
                    "process:",
                    "  name: demo",
                    "  deps:",
                    "    template:",
                    "      path: ./template.md",
                    "      as: path",
                    "      role: template",
                    "    items:",
                    '      path: "{{run.dir}}/items.md"',
                    "      as: path",
                    "      role: items",
                    "      produced_by: setup.out",
                    "    report:",
                    '      path: "{{run.dir}}/report.md"',
                    "      as: path",
                    "      role: run-output",
                    "      produced_by: render.out",
                    "  steps:",
                    "    - id: setup",
                    "      mode: code",
                    "      handler: handler.py:noop",
                    "      outputs:",
                    "        out:",
                    '          path: "{{run.dir}}/items.md"',
                    "    - id: render",
                    "      mode: code",
                    "      handler: handler.py:noop",
                    "      needs: [setup]",
                    "      inputs:",
                    "        items: deps.items",
                    "      outputs:",
                    "        out:",
                    '          path: "{{run.dir}}/report.md"',
                    "---",
                ]
            )
        )

        result = runner.invoke(
            app,
            [
                "deps",
                str(process_dir / "test.process.md"),
                "--var",
                f"RUNS_DIR={tmp_path / 'runs'}",
                "--var",
                "RUN_ID=demo",
            ],
        )

        assert result.exit_code == 0
        assert "Process: demo" in result.output
        assert "template" in result.output
        assert "present" in result.output
        assert "not-yet-due" in result.output

    def test_json_output_includes_state(self, tmp_path: Path) -> None:
        process_dir = tmp_path / "proc"
        process_dir.mkdir()
        (process_dir / "packet.yaml").write_text("packet:\n  kind: demo\n")
        (process_dir / "test.process.md").write_text(
            "\n".join(
                [
                    "---",
                    "process:",
                    "  name: demo",
                    "  deps:",
                    "    packet:",
                    "      path: ./packet.yaml",
                    "      as: path",
                    "      role: packet",
                    "  steps: []",
                    "---",
                ]
            )
        )

        result = runner.invoke(
            app, ["--format", "json", "deps", str(process_dir / "test.process.md")]
        )

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["process"] == "demo"
        assert payload["deps"]["packet"]["state"] == "present"

    def test_json_output_includes_softschema_structure_for_produced_deps(
        self,
        tmp_path: Path,
    ) -> None:
        process_dir = tmp_path / "proc"
        process_dir.mkdir()
        (process_dir / "test.process.md").write_text(
            "\n".join(
                [
                    "---",
                    "process:",
                    "  name: demo",
                    "  deps:",
                    "    packet:",
                    '      path: "{{run.dir}}/packet.md"',
                    "      as: path",
                    "      produced_by: setup.out",
                    "  steps:",
                    "    - id: setup",
                    "      mode: code",
                    "      handler: handler.py:noop",
                    "      outputs:",
                    "        out:",
                    '          path: "{{run.dir}}/packet.md"',
                    "          format: frontmatter-md",
                    "          schema: metaproc:ProcessSpec/0.1",
                    "---",
                ]
            )
        )

        result = runner.invoke(
            app,
            [
                "--format",
                "json",
                "deps",
                str(process_dir / "test.process.md"),
                "--var",
                f"RUNS_DIR={tmp_path / 'runs'}",
                "--var",
                "RUN_ID=demo",
            ],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        dep = payload["deps"]["packet"]
        assert dep["schema"] == "metaproc:ProcessSpec/0.1"
        assert dep["softschema_status"] == "enforced"
        assert dep["softschema_stage"] == "validated_frontmatter"


class TestFingerprintStep:
    def test_runbook_edit_changes_hash(self, tmp_path: Path) -> None:
        runbook = tmp_path / "runbook.md"
        runbook.write_text("# Step 1\n\nDo the thing.\n")

        step = ResolvedStep(
            step_id="s1",
            mode="agent",
            adapter=ResolvedAdapter(type="test", config={}),
            prompt_paths=[str(runbook)],
            outputs={"out": IOSpec(path=f"{tmp_path}/out.md")},
        )

        before = fingerprint_step(step)
        runbook.write_text("# Step 1\n\nDo the thing differently.\n")
        after = fingerprint_step(step)

        assert before != after, "editing runbook bytes must flip the fingerprint"

    def test_missing_referenced_runbook_raises_for_absolute_path(self, tmp_path: Path) -> None:
        """An absolute path that doesn't exist is a real misconfiguration —
        raise loudly so the operator can fix it. A relative path that can't
        be opened from cwd is a softer condition (we may just lack process_dir
        context) and only warns; see ``fingerprint_step`` docstring."""
        missing = tmp_path / "missing.md"
        step = ResolvedStep(
            step_id="s1",
            mode="agent",
            adapter=ResolvedAdapter(type="test", config={}),
            prompt_paths=[str(missing)],
            outputs={"out": IOSpec(path=f"{tmp_path}/out.md")},
        )

        with pytest.raises(FileNotFoundError, match="referenced runbook"):
            fingerprint_step(step)

    def test_missing_relative_runbook_warns_and_skips(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A relative path the caller's cwd can't resolve must NOT crash
        fingerprint_step — production code paths (run-parallel --dry-run,
        deps inspection) call fingerprint from a working directory that
        doesn't always see the process spec's siblings."""
        monkeypatch.chdir(tmp_path)
        step = ResolvedStep(
            step_id="s1",
            mode="agent",
            adapter=ResolvedAdapter(type="test", config={}),
            prompt_paths=["runbook-not-here.md"],  # relative, unreadable from cwd
            outputs={"out": IOSpec(path="out.md")},
        )

        with caplog.at_level("WARNING"):
            fingerprint_step(step)
        assert "not readable from cwd" in caplog.text

    def test_unresolved_template_path_is_skipped(self, tmp_path: Path) -> None:
        """A path still containing ``{{ }}`` is not opened — its literal form
        is already captured by the resolved-step payload, and trying to read it
        would crash on cases like `metaproc deps` invoked without a run."""
        step = ResolvedStep(
            step_id="s1",
            mode="agent",
            adapter=ResolvedAdapter(type="test", config={}),
            prompt_paths=["{{run.dir}}/never-exists.md"],
            outputs={"out": IOSpec(path=f"{tmp_path}/out.md")},
        )

        # Must not raise.
        fingerprint_step(step)

    def test_contract_change_still_changes_hash(self, tmp_path: Path) -> None:
        runbook = tmp_path / "runbook.md"
        runbook.write_text("body")
        step_a = ResolvedStep(
            step_id="s1",
            mode="agent",
            adapter=ResolvedAdapter(type="test", config={}),
            prompt_paths=[str(runbook)],
            outputs={"out": IOSpec(path=f"{tmp_path}/a.md")},
        )
        step_b = step_a.model_copy(update={"outputs": {"out": IOSpec(path=f"{tmp_path}/b.md")}})
        assert fingerprint_step(step_a) != fingerprint_step(step_b)

    def test_composite_uses_path_participates(self, tmp_path: Path) -> None:
        child = tmp_path / "child.process.md"
        child.write_text("---\nprocess:\n  name: child\n  steps: []\n---\n")
        step = ResolvedStep(
            step_id="s1",
            mode="composite",
            adapter=ResolvedAdapter(type="test", config={}),
            uses_path=str(child),
        )
        before = fingerprint_step(step)
        child.write_text("---\nprocess:\n  name: child2\n  steps: []\n---\n")
        after = fingerprint_step(step)
        assert before != after


# ── compute_step_state / recorded_step_hash (Phase 1.4) ──────────


def _write_status_for(run_dir: Path, step_id: str, state: str) -> None:
    sd = run_dir / STATE_DIR / TASKS_SUBDIR / step_id
    sd.mkdir(parents=True, exist_ok=True)
    write_status_at(
        sd,
        StatusRecord(
            run_id="demo/run-1",
            step_id=step_id,
            item={"step": step_id},
            state=cast("StepStatus", state),  # tests exercise raw on-disk states
            started_at="2026-05-20T00:00:00",
            completed_at="2026-05-20T00:01:00" if state in ("completed", "failed") else None,
        ),
    )


def _write_process_status_mirror(
    run_dir: Path,
    step_id: str,
    *,
    state: str,
    recorded_step_hash_value: str | None = None,
) -> None:
    sd = run_dir / STATE_DIR
    sd.mkdir(parents=True, exist_ok=True)
    entry_lines = [f"    state: {state}"]
    if recorded_step_hash_value is not None:
        entry_lines.append(f"    recorded_step_hash: {recorded_step_hash_value}")
    body = (
        "process: demo\n"
        "started_at: '2026-05-20T00:00:00'\n"
        "steps:\n"
        f"  {step_id}:\n" + "\n".join(entry_lines) + "\n"
        "state: completed\n"
    )
    (sd / "process-status.yaml").write_text(body)


class TestRecordedStepHash:
    def test_mirror_value_takes_precedence(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "runs" / "demo"
        run_dir.mkdir(parents=True)
        _write_process_status_mirror(
            run_dir,
            "step-a",
            state="completed",
            recorded_step_hash_value="abc0000000000001",
        )
        # Also write a per-task result.yaml with a *different* hash — mirror wins.
        _write_status_for(run_dir, "step-a", "completed")
        write_result_at(
            run_dir / STATE_DIR / TASKS_SUBDIR / "step-a",
            ResultRecord(
                run_id="demo/run-1",
                step_id="step-a",
                state="completed",
                validated=True,
                outputs={},
                published_at="2026-05-20T00:00:00",
                step_hash="zzz0000000000001",
            ),
        )
        assert recorded_step_hash(run_dir, "step-a") == "abc0000000000001"

    def test_falls_back_to_per_task_result(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "runs" / "demo"
        run_dir.mkdir(parents=True)
        _write_status_for(run_dir, "step-a", "completed")
        write_result_at(
            run_dir / STATE_DIR / TASKS_SUBDIR / "step-a",
            ResultRecord(
                run_id="demo/run-1",
                step_id="step-a",
                state="completed",
                validated=True,
                outputs={},
                published_at="2026-05-20T00:00:00",
                step_hash="legacy0000000003",
            ),
        )
        assert recorded_step_hash(run_dir, "step-a") == "legacy0000000003"

    def test_returns_none_when_nothing_recorded(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "runs" / "demo"
        run_dir.mkdir(parents=True)
        assert recorded_step_hash(run_dir, "step-a") is None


class TestComputeStepState:
    def test_missing_when_no_state_on_disk(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "runs" / "demo"
        run_dir.mkdir(parents=True)
        step = _step("step-a")
        plan = Plan(process="demo", steps=[step])
        assert compute_step_state(run_dir, plan, "step-a") == StepState.missing

    def test_in_flight_from_status_yaml(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "runs" / "demo"
        run_dir.mkdir(parents=True)
        step = _step("step-a")
        plan = Plan(process="demo", steps=[step])
        _write_status_for(run_dir, "step-a", "running")
        assert compute_step_state(run_dir, plan, "step-a") == StepState.in_flight

    def test_in_flight_from_process_status_mirror(self, tmp_path: Path) -> None:
        """Fan-out steps may not write a step-level status.yaml; the mirror
        is the in-flight signal in that layout."""
        run_dir = tmp_path / "runs" / "demo"
        run_dir.mkdir(parents=True)
        step = _step("fan-step")
        plan = Plan(process="demo", steps=[step])
        _write_process_status_mirror(run_dir, "fan-step", state="running")
        assert compute_step_state(run_dir, plan, "fan-step") == StepState.in_flight

    def test_invalidated_from_status_yaml_stale(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "runs" / "demo"
        run_dir.mkdir(parents=True)
        step = _step("step-a")
        plan = Plan(process="demo", steps=[step])
        _write_status_for(run_dir, "step-a", "completed")
        # Simulate _invalidate_downstream renaming.
        sd = run_dir / STATE_DIR / TASKS_SUBDIR / "step-a"
        (sd / STATUS_FILE).rename(sd / "status.yaml.stale")
        assert compute_step_state(run_dir, plan, "step-a") == StepState.invalidated

    def test_current_when_fingerprint_matches(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "runs" / "demo"
        run_dir.mkdir(parents=True)
        step = _step("step-a")
        plan = Plan(process="demo", steps=[step])
        _write_status_for(run_dir, "step-a", "completed")
        _write_process_status_mirror(
            run_dir,
            "step-a",
            state="completed",
            recorded_step_hash_value=fingerprint_step(step),
        )
        assert compute_step_state(run_dir, plan, "step-a") == StepState.current

    def test_stale_when_fingerprint_differs(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "runs" / "demo"
        run_dir.mkdir(parents=True)
        step = _step("step-a")
        plan = Plan(process="demo", steps=[step])
        _write_status_for(run_dir, "step-a", "completed")
        _write_process_status_mirror(
            run_dir,
            "step-a",
            state="completed",
            recorded_step_hash_value="aaaaaaaaaaaaaaaa",
        )
        assert compute_step_state(run_dir, plan, "step-a") == StepState.stale

    def test_current_for_legacy_completion_without_recorded_hash(self, tmp_path: Path) -> None:
        """Completed step with no recorded_step_hash anywhere is treated as a
        legacy completion — current, not stale. Old runs do NOT auto-rerun
        on upgrade."""
        run_dir = tmp_path / "runs" / "demo"
        run_dir.mkdir(parents=True)
        step = _step("step-a")
        plan = Plan(process="demo", steps=[step])
        _write_status_for(run_dir, "step-a", "completed")
        assert compute_step_state(run_dir, plan, "step-a") == StepState.current

    def test_current_when_step_not_in_plan(self, tmp_path: Path) -> None:
        """Plan no longer declares this step (renamed/removed). Treat the
        completion as current rather than inventing a fingerprint."""
        run_dir = tmp_path / "runs" / "demo"
        run_dir.mkdir(parents=True)
        plan = Plan(process="demo", steps=[_step("other-step")])
        _write_status_for(run_dir, "ghost-step", "completed")
        _write_process_status_mirror(
            run_dir,
            "ghost-step",
            state="completed",
            recorded_step_hash_value="aaaaaaaaaaaaaaaa",
        )
        assert compute_step_state(run_dir, plan, "ghost-step") == StepState.current

    def test_failed_step_is_missing(self, tmp_path: Path) -> None:
        """A step that started and failed has no completion record — it's
        ``missing`` for status purposes."""
        run_dir = tmp_path / "runs" / "demo"
        run_dir.mkdir(parents=True)
        step = _step("step-a")
        plan = Plan(process="demo", steps=[step])
        _write_status_for(run_dir, "step-a", "failed")
        assert compute_step_state(run_dir, plan, "step-a") == StepState.missing

    def test_invalidated_wins_over_in_flight_via_stale_marker(self, tmp_path: Path) -> None:
        """Order: in_flight beats invalidated. If a step both has a running
        item AND a stale marker, surface it as in_flight (it's actively
        being re-executed)."""
        run_dir = tmp_path / "runs" / "demo"
        run_dir.mkdir(parents=True)
        step = _step("step-a")
        plan = Plan(process="demo", steps=[step])
        _write_status_for(run_dir, "step-a", "running")
        # Also create a sibling stale marker as if a previous attempt was invalidated.
        sd = run_dir / STATE_DIR / TASKS_SUBDIR / "step-a"
        (sd / "old.status.yaml.stale").write_text("dummy")
        assert compute_step_state(run_dir, plan, "step-a") == StepState.in_flight
