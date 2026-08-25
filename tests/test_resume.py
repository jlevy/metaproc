"""Resume contract integration tests.

Tests that the filesystem-first resume contract works correctly across
scenarios: local rerun (completed steps skipped), stale lease takeover,
dispatch manifest survival, and cross-topology resume.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from strif import atomic_output_file

from metaproc.commands.run_process import (
    _is_step_completed,
    _validate_run_config,
    _write_run_config,
)
from metaproc.errors import CLIError
from metaproc.io import read_yaml_file, to_yaml_string
from metaproc.io.dispatch_manifest import read_dispatch_manifest, write_dispatch_manifest
from metaproc.io.orchestrator_lease import (
    LeaseHeartbeat,
    acquire_lease,
    release_lease,
)
from metaproc.io.state_io import write_status_at
from metaproc.models.runtime import StatusRecord
from metaproc.paths import (
    ORCHESTRATOR_LEASE_FILE,
    RUN_CONFIG_FILE,
    STATE_DIR,
    TASKS_SUBDIR,
)

# ── Helpers ──────────────────────────────────────────────────────


def _step_state_dir_for(run_dir: Path, step_id: str) -> Path:
    """Return the per-task state dir for a non-fan-out step."""
    return run_dir / STATE_DIR / TASKS_SUBDIR / step_id


def _write_step_completed(run_dir: Path, step_id: str, run_id: str = "test-run") -> None:
    """Write a completed status record for a step."""
    state_dir = _step_state_dir_for(run_dir, step_id)
    state_dir.mkdir(parents=True, exist_ok=True)
    record = StatusRecord(
        run_id=run_id,
        step_id=step_id,
        item={"_step": step_id},
        state="completed",
        started_at="2026-04-12T00:00:00",
        completed_at="2026-04-12T00:01:00",
    )
    write_status_at(state_dir, record)


def _write_step_failed(run_dir: Path, step_id: str, run_id: str = "test-run") -> None:
    """Write a failed status record for a step."""
    state_dir = _step_state_dir_for(run_dir, step_id)
    state_dir.mkdir(parents=True, exist_ok=True)
    record = StatusRecord(
        run_id=run_id,
        step_id=step_id,
        item={"_step": step_id},
        state="failed",
        started_at="2026-04-12T00:00:00",
        error="simulated failure",
    )
    write_status_at(state_dir, record)


def _write_run_config_helper(
    run_dir: Path, *, backend: str = "local", variant: str | None = None
) -> Path:
    """Write a run-config for test setup."""
    return _write_run_config(
        run_dir,
        process_name="mine",
        process_path=Path("process/mine/mine.process.md"),
        run_id="test-run",
        variables={"RUN_ID": "test-run", "DATASET": "test-data"},
        backend=backend,
        variant=variant,
    )


def _write_stale_lease(run_dir: Path) -> Path:
    """Write a lease with an expired heartbeat."""
    state_dir = run_dir / STATE_DIR
    state_dir.mkdir(parents=True, exist_ok=True)
    lease_path = state_dir / ORCHESTRATOR_LEASE_FILE
    data = {
        "owner_type": "local",
        "owner_host": "crashed-host",
        "owner_pid": 99999,
        "started_at": "2020-01-01T00:00:00",
        "last_heartbeat_at": "2020-01-01T00:00:00",
        "command_summary": "run-process mine --backend gcp-worker",
    }
    with atomic_output_file(lease_path) as tmp:
        Path(tmp).write_text(to_yaml_string(data))
    return lease_path


# ── Local rerun: completed steps are skipped ─────────────────────


class TestLocalRerun:
    """Verify that completed steps are detected and skipped on resume."""

    def test_completed_step_is_detected(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-1" / "mine"
        run_dir.mkdir(parents=True)

        _write_step_completed(run_dir, "generate-record")
        assert _is_step_completed(run_dir, "generate-record") is True

    def test_incomplete_step_is_not_skipped(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-1" / "mine"
        run_dir.mkdir(parents=True)

        assert _is_step_completed(run_dir, "generate-record") is False

    def test_failed_step_is_not_skipped(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-1" / "mine"
        run_dir.mkdir(parents=True)

        _write_step_failed(run_dir, "generate-record")
        assert _is_step_completed(run_dir, "generate-record") is False

    def test_mixed_steps_only_completed_are_skipped(self, tmp_path: Path) -> None:
        """In a multi-step process, only completed steps are skipped."""
        run_dir = tmp_path / "run-1" / "mine"
        run_dir.mkdir(parents=True)

        _write_step_completed(run_dir, "step-a")
        _write_step_completed(run_dir, "step-b")
        _write_step_failed(run_dir, "step-c")
        # step-d has no status at all

        assert _is_step_completed(run_dir, "step-a") is True
        assert _is_step_completed(run_dir, "step-b") is True
        assert _is_step_completed(run_dir, "step-c") is False
        assert _is_step_completed(run_dir, "step-d") is False

    def test_run_config_preserved_across_resume(self, tmp_path: Path) -> None:
        """Run-config is written once and validated (not overwritten) on resume."""
        run_dir = tmp_path / "run-1" / "mine"
        run_dir.mkdir(parents=True)

        first = _write_run_config_helper(run_dir)
        first_data = read_yaml_file(first)

        # Simulate resume — same params.
        second = _write_run_config_helper(run_dir)
        second_data = read_yaml_file(second)

        assert first_data["created_at"] == second_data["created_at"]


# ── Interrupted orchestrator: lease stale takeover ───────────────


class TestInterruptedOrchestrator:
    """Simulate an orchestrator crash and resume via stale lease takeover."""

    def test_stale_lease_allows_resume(self, tmp_path: Path) -> None:
        """A stale lease from a crashed orchestrator can be taken over."""
        run_dir = tmp_path / "run-1" / "mine"
        run_dir.mkdir(parents=True)

        _write_run_config_helper(run_dir, backend="gcp-worker")
        _write_step_completed(run_dir, "step-a")
        _write_stale_lease(run_dir)

        # Resume: acquire lease should take over the stale one.
        lease_path = acquire_lease(run_dir, command_summary="resume")
        data = read_yaml_file(lease_path)
        assert data["owner_pid"] == os.getpid()

        # Completed step is still detected.
        assert _is_step_completed(run_dir, "step-a") is True

        release_lease(run_dir)

    def test_live_lease_blocks_resume(self, tmp_path: Path) -> None:
        """A live lease from another orchestrator blocks resume."""
        run_dir = tmp_path / "run-1" / "mine"
        run_dir.mkdir(parents=True)

        acquire_lease(run_dir, command_summary="first orchestrator")

        with pytest.raises(CLIError, match="Another orchestrator holds the lease"):
            acquire_lease(run_dir, command_summary="second orchestrator")

        release_lease(run_dir)

    def test_force_overrides_live_lease_on_resume(self, tmp_path: Path) -> None:
        """--force lets a new orchestrator take over a live lease."""
        run_dir = tmp_path / "run-1" / "mine"
        run_dir.mkdir(parents=True)

        acquire_lease(run_dir, command_summary="stuck")
        lease_path = acquire_lease(run_dir, command_summary="forced resume", force=True)

        data = read_yaml_file(lease_path)
        assert data["command_summary"] == "forced resume"

        release_lease(run_dir)

    def test_lease_heartbeat_keeps_lease_alive(self, tmp_path: Path) -> None:
        """LeaseHeartbeat context manager keeps the lease fresh."""
        run_dir = tmp_path / "run-1" / "mine"
        run_dir.mkdir(parents=True)

        acquire_lease(run_dir)
        with LeaseHeartbeat(run_dir, interval=1):
            time.sleep(0.1)
        # Lease should still be readable after context exits.
        data = read_yaml_file(run_dir / STATE_DIR / ORCHESTRATOR_LEASE_FILE)
        assert data["owner_pid"] == os.getpid()

        release_lease(run_dir)


# ── Cloud interrupted: dispatch manifest survives ────────────────


class TestCloudInterrupted:
    """Dispatch manifest written before crash is readable on resume."""

    def test_manifest_survives_crash(self, tmp_path: Path) -> None:
        """Manifest written during dispatch is readable after simulated crash."""
        run_dir = tmp_path / "run-1" / "mine"
        run_dir.mkdir(parents=True)

        worker_jobs = [
            {
                "worker_id": "0",
                "job_name": "projects/p/locations/r/jobs/j0",
                "job_id": "j0",
                "items_count": "25",
            },
            {
                "worker_id": "1",
                "job_name": "projects/p/locations/r/jobs/j1",
                "job_id": "j1",
                "items_count": "25",
            },
        ]
        write_dispatch_manifest(
            run_dir, "generate-record", worker_jobs=worker_jobs, num_items=50, variant="pi-glm-5"
        )

        # Simulate crash: lease gone, orchestrator dead.
        # On resume, the manifest should be readable.
        data = read_dispatch_manifest(run_dir, "generate-record")
        assert data is not None
        assert data["step_id"] == "generate-record"
        assert data["num_items"] == 50
        assert data["num_workers"] == 2
        assert data["variant"] == "pi-glm-5"
        workers = data["workers"]
        assert isinstance(workers, list)
        assert len(workers) == 2
        assert workers[0]["job_id"] == "j0"  # pyright: ignore[reportIndexIssue]
        assert workers[1]["job_id"] == "j1"  # pyright: ignore[reportIndexIssue]

    def test_manifest_absent_for_unstarted_step(self, tmp_path: Path) -> None:
        """Steps that were never dispatched have no manifest."""
        run_dir = tmp_path / "run-1" / "mine"
        run_dir.mkdir(parents=True)

        assert read_dispatch_manifest(run_dir, "never-started") is None

    def test_completed_step_with_manifest(self, tmp_path: Path) -> None:
        """A step can be both completed and have a manifest (workers finished)."""
        run_dir = tmp_path / "run-1" / "mine"
        run_dir.mkdir(parents=True)

        _write_step_completed(run_dir, "generate-record")
        worker_jobs = [
            {"worker_id": "0", "job_name": "j0", "job_id": "j0", "items_count": "10"},
        ]
        write_dispatch_manifest(run_dir, "generate-record", worker_jobs=worker_jobs, num_items=10)

        # Step is completed — should be skipped on resume.
        assert _is_step_completed(run_dir, "generate-record") is True
        # Manifest still readable for inspection.
        data = read_dispatch_manifest(run_dir, "generate-record")
        assert data is not None


# ── Cross-topology resume ────────────────────────────────────────


class TestBackendAgnosticResumeState:
    """Run identity and state remain independent of the execution backend."""

    def test_run_config_allows_different_backend(self, tmp_path: Path) -> None:
        """Run-config validates process + run_dir, not backend.

        Backend is execution metadata rather than part of the durable run
        identity; supported launch paths enforce topology separately.
        """
        run_dir = tmp_path / "run-1" / "mine"
        run_dir.mkdir(parents=True)

        # Write config with local backend.
        _write_run_config_helper(run_dir, backend="local")

        # Validate with different backend — should NOT raise.
        config_path = run_dir / STATE_DIR / RUN_CONFIG_FILE
        _validate_run_config(config_path, process_name="mine", run_dir=run_dir)

    def test_run_config_allows_different_variant(self, tmp_path: Path) -> None:
        """Variant is not part of the resume identity check."""
        run_dir = tmp_path / "run-1" / "mine"
        run_dir.mkdir(parents=True)

        _write_run_config_helper(run_dir, variant="pi-glm-5")

        # Validate — variant is not checked.
        config_path = run_dir / STATE_DIR / RUN_CONFIG_FILE
        _validate_run_config(config_path, process_name="mine", run_dir=run_dir)

    def test_run_config_rejects_different_process(self, tmp_path: Path) -> None:
        """Cross-topology resume must still match the process identity."""
        run_dir = tmp_path / "run-1" / "mine"
        run_dir.mkdir(parents=True)

        _write_run_config_helper(run_dir)

        config_path = run_dir / STATE_DIR / RUN_CONFIG_FILE
        with pytest.raises(CLIError, match="Resume mismatch.*process"):
            _validate_run_config(config_path, process_name="retro", run_dir=run_dir)

    def test_step_state_shared_across_topologies(self, tmp_path: Path) -> None:
        """Step completion state is topology-agnostic (just filesystem).

        A step completed by cloud workers is detected as completed by a
        local orchestrator resuming the same run_dir.
        """
        run_dir = tmp_path / "run-1" / "mine"
        run_dir.mkdir(parents=True)

        # Simulate: cloud workers completed step-a.
        _write_step_completed(run_dir, "step-a")

        # Local orchestrator checks — should see it as completed.
        assert _is_step_completed(run_dir, "step-a") is True

    def test_full_resume_flow(self, tmp_path: Path) -> None:
        """End-to-end: config, lease, and step state survive an interrupted run.

        Simulates a Batch orchestrator crashing mid-run and a new orchestrator
        resuming the same durable run directory.
        """
        run_dir = tmp_path / "run-1" / "mine"
        run_dir.mkdir(parents=True)

        # Phase 1: Original run (gcp-worker backend).
        _write_run_config_helper(run_dir, backend="gcp-worker", variant="pi-glm-5")
        _write_step_completed(run_dir, "step-a")
        _write_step_failed(run_dir, "step-b")
        worker_jobs = [
            {"worker_id": "0", "job_name": "j0", "job_id": "j0", "items_count": "10"},
        ]
        write_dispatch_manifest(run_dir, "step-b", worker_jobs=worker_jobs, num_items=10)
        _write_stale_lease(run_dir)

        # Phase 2: Resume from a replacement orchestrator.
        # Validate run identity.
        config_path = run_dir / STATE_DIR / RUN_CONFIG_FILE
        _validate_run_config(config_path, process_name="mine", run_dir=run_dir)

        # Take over stale lease.
        lease_path = acquire_lease(run_dir, command_summary="resume attempt")
        assert lease_path.exists()

        # Check step states.
        assert _is_step_completed(run_dir, "step-a") is True
        assert _is_step_completed(run_dir, "step-b") is False

        # Read dispatch manifest for failed step.
        manifest = read_dispatch_manifest(run_dir, "step-b")
        assert manifest is not None
        workers = manifest["workers"]
        assert isinstance(workers, list)
        assert workers[0]["job_id"] == "j0"  # pyright: ignore[reportIndexIssue]

        release_lease(run_dir)
