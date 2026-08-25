"""End-to-end test for the step-fingerprint edit-and-rerun cascade.

Spec: ``docs/arch/arch-metaproc-core.md``
(Phase 1.5 — "End-to-end test: edit step-2 runbook, rerun, only step 2+3
re-execute").

Drives the ``fingerprint_smoke`` fixture against a real temp ``RUNS_DIR``
(no mock filesystem). Asserts that mutating step 2's runbook between two
otherwise-identical runs cascades correctly: step 1 stays cached; steps 2
and 3 re-execute and pick up new fingerprints in their per-step records.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.io.state_io import read_result_at
from metaproc.paths import STATE_DIR, TASKS_SUBDIR

_FIXTURE_SRC = Path(__file__).parent / "fixtures" / "fingerprint_smoke"


def _stage_fixture(dst: Path) -> Path:
    """Copy the fixture process + handler + runbooks into *dst* so the test
    can mutate them without touching the checked-in source."""
    dst.mkdir(parents=True, exist_ok=True)
    for name in (
        "fingerprint-smoke.process.md",
        "fingerprint_smoke_handlers.py",
        "step1-runbook.md",
        "step2-runbook.md",
        "step3-runbook.md",
    ):
        shutil.copy(_FIXTURE_SRC / name, dst / name)
    return dst / "fingerprint-smoke.process.md"


def _run(
    process_path: Path,
    runs_dir: Path,
    run_id: str,
) -> tuple[int, str]:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run-process",
            str(process_path),
            "--var",
            f"RUNS_DIR={runs_dir}",
            "--var",
            f"RUN_ID={run_id}",
            "--backend",
            "local",
        ],
    )
    return result.exit_code, result.output


def _read_step_hash(run_dir: Path, step_id: str) -> str | None:
    state_dir = run_dir / STATE_DIR / TASKS_SUBDIR / step_id
    record = read_result_at(state_dir)
    if record is None:
        return None
    return record.step_hash


def _read_process_status_hash(run_dir: Path, step_id: str) -> str | None:
    data = yaml.safe_load((run_dir / STATE_DIR / "process-status.yaml").read_text())
    return data["steps"][step_id].get("recorded_step_hash")


def test_edit_step2_runbook_cascades_to_step3_only(tmp_path: Path) -> None:
    process_path = _stage_fixture(tmp_path / "proc")
    runs_dir = tmp_path / "runs"
    run_id = "fingerprint-smoke-run"
    run_dir = runs_dir / run_id

    # First run — all three steps execute.
    exit_code, output = _run(process_path, runs_dir, run_id)
    assert exit_code == 0, f"first run failed: {output}"

    invocations_path = run_dir / "invocations.log"
    assert invocations_path.exists()
    first_invocations = invocations_path.read_text().splitlines()
    assert first_invocations == ["s1", "s2", "s3"], first_invocations

    s1_hash_v1 = _read_step_hash(run_dir, "s1")
    s2_hash_v1 = _read_step_hash(run_dir, "s2")
    s3_hash_v1 = _read_step_hash(run_dir, "s3")
    assert s1_hash_v1
    assert s2_hash_v1
    assert s3_hash_v1

    # process-status.yaml mirror was populated by the orchestrator.
    assert _read_process_status_hash(run_dir, "s1") == s1_hash_v1
    assert _read_process_status_hash(run_dir, "s2") == s2_hash_v1
    assert _read_process_status_hash(run_dir, "s3") == s3_hash_v1

    # Edit step 2's runbook — the only change between runs.
    (process_path.parent / "step2-runbook.md").write_text(
        "# Step 2 runbook\n\nNow with a different body to flip the fingerprint.\n"
    )

    # Second run — same RUN_ID.
    exit_code, output = _run(process_path, runs_dir, run_id)
    assert exit_code == 0, f"second run failed: {output}"

    second_invocations = invocations_path.read_text().splitlines()
    # Exactly s2 and s3 ran again; s1 stayed cached.
    assert second_invocations == ["s1", "s2", "s3", "s2", "s3"], second_invocations

    # s1's recorded fingerprint is unchanged (the file wasn't touched).
    assert _read_step_hash(run_dir, "s1") == s1_hash_v1
    assert _read_process_status_hash(run_dir, "s1") == s1_hash_v1

    # s2's recorded fingerprint flipped to the new value (runbook bytes changed).
    s2_hash_v2 = _read_step_hash(run_dir, "s2")
    assert s2_hash_v2 is not None
    assert s2_hash_v2 != s2_hash_v1, "step 2 fingerprint must change with runbook edit"
    assert _read_process_status_hash(run_dir, "s2") == s2_hash_v2

    # s3's fingerprint is computed from its own (unedited) runbook + its model,
    # so the *value* stays the same — but its result.yaml was rewritten (the
    # step re-executed). The invocations log already proved it ran again; here
    # we lock the contract that the recorded hash matches whatever the current
    # fingerprint is.
    s3_hash_v2 = _read_step_hash(run_dir, "s3")
    assert s3_hash_v2 == s3_hash_v1
    assert _read_process_status_hash(run_dir, "s3") == s3_hash_v2


def test_status_steps_works_from_unrelated_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``metaproc status --steps`` must rebuild the plan regardless of the
    cwd it's invoked from. Earlier behavior persisted ``process_spec`` as a
    relative string, so launching from one cwd and inspecting from another
    silently degraded to ``Steps: (no plan)``.
    """
    fixture_dir = tmp_path / "proc"
    _stage_fixture(fixture_dir)
    runs_dir = tmp_path / "runs"
    run_id = "fingerprint-cwd-test"
    run_dir = runs_dir / run_id

    # Launch the run via the *relative* spec path from the fixture dir to
    # exercise the worst case: a relative path that only resolves from one
    # cwd. The orchestrator must persist an absolute spec path so a later
    # status invocation from anywhere can rebuild the plan.
    monkeypatch.chdir(fixture_dir)
    runner = CliRunner()
    launch = runner.invoke(
        app,
        [
            "run-process",
            "fingerprint-smoke.process.md",  # relative — would break old behavior
            "--var",
            f"RUNS_DIR={runs_dir}",
            "--var",
            f"RUN_ID={run_id}",
            "--backend",
            "local",
        ],
    )
    assert launch.exit_code == 0, launch.output

    # Now move to an unrelated cwd and invoke `metaproc status --steps`.
    # If process_spec were stored relative, _load_plan_from_run would fail
    # and the Steps table would collapse to "(no plan)".
    unrelated = tmp_path / "elsewhere"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)

    status = runner.invoke(app, ["status", str(run_dir), "--steps"])
    assert status.exit_code == 0, status.output
    assert "no plan" not in status.output.lower(), (
        f"status --steps degraded to no-plan from unrelated cwd:\n{status.output}"
    )
    for step_id in ("s1", "s2", "s3"):
        assert step_id in status.output, f"expected {step_id} in steps table:\n{status.output}"


def test_status_rebuilds_plan_with_recorded_execution_identity(tmp_path: Path) -> None:
    """A completed profile-selected run must remain current in ``status``.

    The operator may select a non-default execution profile and a separate
    artifact namespace. Both affect the resolved step fingerprint, so status
    must rebuild with the immutable identity captured in run-config.yaml.
    """
    process_path = _stage_fixture(tmp_path / "proc")
    runs_dir = tmp_path / "runs"
    run_id = "fingerprint-profile-status"
    runner = CliRunner()

    launch = runner.invoke(
        app,
        [
            "run-process",
            str(process_path),
            "--var",
            f"RUNS_DIR={runs_dir}",
            "--var",
            f"RUN_ID={run_id}",
            "--variant",
            "gemini-flash-36",
            "--artifact-namespace",
            "status-test-artifacts",
            "--backend",
            "local",
        ],
    )
    assert launch.exit_code == 0, launch.output

    status = runner.invoke(
        app,
        ["status", str(runs_dir / run_id), "--steps", "--format", "json"],
    )
    assert status.exit_code == 0, status.output
    payload = yaml.safe_load(status.output)
    assert payload["process_state"] == "current"
    assert {step["state"] for step in payload["steps"]} == {"current"}


def test_resume_with_unchanged_runbooks_skips_everything(tmp_path: Path) -> None:
    """No edits between runs → all three steps cached on the second run.

    Locks the "legacy resume keeps working" contract: a clean rerun must NOT
    re-execute anything just because the fingerprint check now exists.
    """
    process_path = _stage_fixture(tmp_path / "proc")
    runs_dir = tmp_path / "runs"
    run_id = "fingerprint-clean-resume"
    run_dir = runs_dir / run_id

    exit_code, output = _run(process_path, runs_dir, run_id)
    assert exit_code == 0, output
    invocations_path = run_dir / "invocations.log"
    assert invocations_path.read_text().splitlines() == ["s1", "s2", "s3"]

    exit_code, output = _run(process_path, runs_dir, run_id)
    assert exit_code == 0, output
    # No new invocations.
    assert invocations_path.read_text().splitlines() == ["s1", "s2", "s3"]


# ── CLI flag plumbing for status --steps / --stale-only ──────────


def _launch_smoke_run(tmp_path: Path) -> Path:
    """Run the smoke fixture once and return the run dir."""
    process_path = _stage_fixture(tmp_path / "proc")
    runs_dir = tmp_path / "runs"
    run_id = "fingerprint-cli-test"
    exit_code, output = _run(process_path, runs_dir, run_id)
    assert exit_code == 0, output
    return runs_dir / run_id


def test_status_format_json_steps_projects_to_steps_surface(tmp_path: Path) -> None:
    """``--steps --format json`` returns just ``{run_dir, process_state,
    steps}``. Operators scripting against the steps surface should not
    have to dig past variant/timing/system noise."""
    run_dir = _launch_smoke_run(tmp_path)
    runner = CliRunner()

    full = runner.invoke(app, ["status", str(run_dir), "--format", "json"])
    assert full.exit_code == 0
    full_payload = yaml.safe_load(full.output)
    assert "variants" in full_payload
    assert "totals" in full_payload

    projected = runner.invoke(app, ["status", str(run_dir), "--steps", "--format", "json"])
    assert projected.exit_code == 0
    projected_payload = yaml.safe_load(projected.output)
    assert set(projected_payload.keys()) == {"run_dir", "process_state", "steps"}
    assert [s["step_id"] for s in projected_payload["steps"]] == ["s1", "s2", "s3"]


def test_status_format_json_stale_only_filters_steps_array(tmp_path: Path) -> None:
    """``--stale-only --format json`` keeps the full payload but drops
    ``current`` / ``missing`` rows from ``steps[]``."""
    process_path = _stage_fixture(tmp_path / "proc")
    runs_dir = tmp_path / "runs"
    run_id = "fingerprint-cli-stale"
    exit_code, output = _run(process_path, runs_dir, run_id)
    assert exit_code == 0, output

    # Edit s2's runbook so it shows up as stale on the next status read.
    (process_path.parent / "step2-runbook.md").write_text("# Step 2\n\nbody v2\n")
    run_dir = runs_dir / run_id

    runner = CliRunner()
    result = runner.invoke(app, ["status", str(run_dir), "--stale-only", "--format", "json"])
    assert result.exit_code == 0
    payload = yaml.safe_load(result.output)
    step_ids = [s["step_id"] for s in payload["steps"]]
    assert step_ids == ["s2"]
    assert payload["steps"][0]["state"] == "stale"


def test_status_steps_text_suppresses_variant_block_via_cli(tmp_path: Path) -> None:
    """End-to-end check that ``--steps`` actually trims the text output
    (the golden test covers the formatter in isolation; this exercises
    the CLI wiring)."""
    run_dir = _launch_smoke_run(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["status", str(run_dir), "--steps"])
    assert result.exit_code == 0
    assert "Variant" not in result.output
    assert result.output.lstrip().startswith("Steps:")
