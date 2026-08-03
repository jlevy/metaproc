"""Tests for consolidated metaproc gcp commands (status, logs, cancel, runs).

Tests the auto-detect pattern: each command accepts a <target> that is
either a local run directory (path exists) or a run-id string (query Batch API).
"""

# pyright: reportAttributeAccessIssue=false

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer
from google.cloud import filestore_v1
from google.cloud.batch_v1.types import AllocationPolicy, JobStatus
from typer.main import get_command
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.cloud.gcp.batch_backend import run_identity_label, sanitize_label
from metaproc.commands.gcp import (
    _ASSET_TYPES,
    _build_env_exports,
    _build_logs_filter,
    _build_worktree_script,
    _check_clean_branch,
    _ensure_remote_repo,
    _extract_job_names,
    _format_job_results,
    _infer_scale_spot_from_manifest,
    _is_run_dir,
    _list_filestore_instances,
    _query_filestore_utilization,
    _query_jobs_by_run_id,
    _read_events,
    _remote_editable_install_command,
    _resolve_gateway_host,
    _resolve_job_names_and_project,
    _resolve_scale_run_dir,
    _search_resources,
)
from metaproc.errors import CLIError
from metaproc.io.dispatch_manifest import write_dispatch_manifest
from metaproc.paths import (
    RUN_CONFIG_FILE,
    SCALE_OVERRIDE_FILE,
    SCALE_STATE_FILE,
    STATE_DIR,
    runpool_events,
    step_state_dir,
    worker_state_dir,
)
from metaproc.runpool.status import read_scale_override, read_scale_state

# ── Helper tests ─────────────────────────────────────────────────


class TestIsRunDir:
    def test_existing_dir(self, tmp_path: Path):
        assert _is_run_dir(str(tmp_path)) is True

    def test_nonexistent_dir(self):
        assert _is_run_dir("nonexistent-run-id-string") is False

    def test_run_id_string(self):
        assert _is_run_dir("mine-2026-04-09") is False


class TestReadEvents:
    def test_empty_dir(self, tmp_path: Path):
        assert _read_events(tmp_path) == []

    def test_reads_events(self, tmp_path: Path):

        events_file = runpool_events(tmp_path)
        events_file.parent.mkdir(parents=True)
        events = [
            {"event": "process_start", "external_id": "projects/p/locations/r/jobs/j1"},
            {"event": "process_start", "external_id": "projects/p/locations/r/jobs/j2"},
        ]
        events_file.write_text("\n".join(json.dumps(e) for e in events))

        result = _read_events(tmp_path)
        assert len(result) == 2


class TestExtractJobNames:
    def test_extracts_batch_jobs(self):
        events: list[dict[str, object]] = [
            {"event": "process_start", "external_id": "projects/p/locations/r/jobs/j1"},
            {"event": "process_start", "external_id": "projects/p/locations/r/jobs/j2"},
            {"event": "process_end"},  # no external_id
        ]
        names = _extract_job_names(events)
        assert names == [
            "projects/p/locations/r/jobs/j1",
            "projects/p/locations/r/jobs/j2",
        ]


# ── Status command (auto-detect) ────────────────────────────────


class TestFormatJobResults:
    def test_json_output(self):

        mock_job = MagicMock()
        mock_job.labels = {"metaproc-role": "orchestrator", "metaproc-run-id": "test"}
        mock_job.status.state = JobStatus.State.RUNNING
        mock_job.name = "projects/p/locations/r/jobs/mp-orch-test-123"

        out = MagicMock()
        _format_job_results([mock_job], run_id="test", failed_only=False, as_json=True, out=out)
        # Should call out.data with JSON
        out.data.assert_called_once()
        data = json.loads(out.data.call_args[0][0])
        assert len(data) == 1
        assert data[0]["role"] == "orchestrator"
        assert data[0]["state"] == "RUNNING"

    def test_failed_only_filter(self):

        running = MagicMock()
        running.labels = {"metaproc-role": "worker", "metaproc-run-id": "test"}
        running.status.state = JobStatus.State.RUNNING
        running.name = "projects/p/locations/r/jobs/j1"

        failed = MagicMock()
        failed.labels = {"metaproc-role": "worker", "metaproc-run-id": "test"}
        failed.status.state = JobStatus.State.FAILED
        failed.name = "projects/p/locations/r/jobs/j2"

        out = MagicMock()
        _format_job_results(
            [running, failed], run_id="test", failed_only=True, as_json=True, out=out
        )
        data = json.loads(out.data.call_args[0][0])
        assert len(data) == 1
        assert data[0]["state"] == "FAILED"


# ── Resolve job names (auto-detect) ─────────────────────────────


class TestResolveJobNamesAndProject:
    def test_local_dir_mode(self, tmp_path: Path):

        events_file = runpool_events(tmp_path)
        events_file.parent.mkdir(parents=True)
        events = [
            {"event": "process_start", "external_id": "projects/myproj/locations/r/jobs/j1"},
        ]
        events_file.write_text(json.dumps(events[0]))

        job_names, project = _resolve_job_names_and_project(str(tmp_path), "", "us-central1")
        assert job_names == ["projects/myproj/locations/r/jobs/j1"]
        assert project == "myproj"

    def test_run_id_mode(self):
        mock_job = MagicMock()
        mock_job.name = "projects/p/locations/r/jobs/j1"

        with (
            patch(
                "metaproc.commands.gcp._query_jobs_by_run_id",
                return_value=[mock_job],
            ),
            patch.dict("os.environ", {"METAPROC_GCP_PROJECT": "test-project"}),
        ):
            job_names, project = _resolve_job_names_and_project(
                "mine-2026-04-09", "", "us-central1"
            )

        assert job_names == ["projects/p/locations/r/jobs/j1"]
        assert project == "test-project"


class TestRunIdentityLookup:
    def test_colliding_readable_labels_use_distinct_exact_queries(self) -> None:
        assert sanitize_label("run_abc") == sanitize_label("run-abc")
        assert run_identity_label("run_abc") != run_identity_label("run-abc")

        client = MagicMock()
        exact_job = MagicMock()
        client.list_jobs.return_value = [exact_job]
        with patch("google.cloud.batch_v1.BatchServiceClient", return_value=client):
            jobs = _query_jobs_by_run_id("run_abc", "project", "region")

        assert jobs == [exact_job]
        request = client.list_jobs.call_args.kwargs["request"]
        assert run_identity_label("run_abc") in request.filter
        assert "metaproc-run-key" in request.filter

    def test_legacy_fallback_excludes_modern_jobs_with_colliding_readable_label(self) -> None:
        legacy_job = MagicMock()
        legacy_job.labels = {"metaproc-run-id": "run-abc"}
        modern_collision = MagicMock()
        modern_collision.labels = {
            "metaproc-run-id": "run-abc",
            "metaproc-run-key": "v1-other",
        }
        client = MagicMock()
        client.list_jobs.side_effect = [[], [legacy_job, modern_collision]]

        with patch("google.cloud.batch_v1.BatchServiceClient", return_value=client):
            jobs = _query_jobs_by_run_id("run_abc", "project", "region")

        assert jobs == [legacy_job]
        assert client.list_jobs.call_count == 2
        fallback_request = client.list_jobs.call_args.kwargs["request"]
        assert 'labels.metaproc-run-id="run-abc"' == fallback_request.filter


class TestGcpRunsIdentity:
    @staticmethod
    def _job(
        run_id: str,
        job_id: str,
        *,
        include_exact_metadata: bool = True,
        identity_key: str | None = None,
    ) -> MagicMock:
        job = MagicMock()
        job.name = f"projects/p/locations/r/jobs/{job_id}"
        job.labels = {
            "metaproc-run-id": sanitize_label(run_id),
            "metaproc-run-key": identity_key or run_identity_label(run_id),
            "metaproc-role": "orchestrator",
        }
        job.status.state = JobStatus.State.RUNNING
        if include_exact_metadata:
            environment = SimpleNamespace(
                variables={"METAPROC_VARS": json.dumps({"RUN_ID": run_id})}
            )
            runnable = SimpleNamespace(environment=environment)
            task_spec = SimpleNamespace(runnables=[runnable])
            job.task_groups = [SimpleNamespace(task_spec=task_spec)]
        else:
            job.task_groups = []
        return job

    def test_exact_metadata_keeps_colliding_and_dot_separated_ids_distinct(self) -> None:
        dot_id = "run-20260803T010203Z.1234560000.abc123def4"
        jobs = [
            self._job("run_abc", "underscore"),
            self._job("run-abc", "dash"),
            self._job(dot_id, "timestamped"),
        ]
        client = MagicMock()
        client.list_jobs.return_value = jobs

        with (
            patch("metaproc.commands.gcp._require_gcp_batch"),
            patch("google.cloud.batch_v1.BatchServiceClient", return_value=client),
        ):
            result = CliRunner().invoke(
                app,
                ["gcp", "runs", "--project", "p", "--json"],
            )

        assert result.exit_code == 0, result.output
        inventory = json.loads(result.output)
        assert set(inventory) == {"run_abc", "run-abc", dot_id}
        assert inventory["run_abc"][0]["job_id"] == "underscore"
        assert inventory["run-abc"][0]["job_id"] == "dash"
        assert inventory[dot_id][0]["job_id"] == "timestamped"

    def test_unreadable_modern_metadata_falls_back_to_distinct_identity_keys(self) -> None:
        jobs = [
            self._job(
                "run_abc",
                "first",
                include_exact_metadata=False,
                identity_key="v1-first",
            ),
            self._job(
                "run-abc",
                "second",
                include_exact_metadata=False,
                identity_key="v1-second",
            ),
        ]
        client = MagicMock()
        client.list_jobs.return_value = jobs

        with (
            patch("metaproc.commands.gcp._require_gcp_batch"),
            patch("google.cloud.batch_v1.BatchServiceClient", return_value=client),
        ):
            result = CliRunner().invoke(
                app,
                ["gcp", "runs", "--project", "p", "--json"],
            )

        assert result.exit_code == 0, result.output
        inventory = json.loads(result.output)
        assert set(inventory) == {
            "run-abc [v1-first]",
            "run-abc [v1-second]",
        }

    def test_legacy_job_without_identity_key_keeps_readable_group(self) -> None:
        job = self._job("legacy-run-id", "legacy", include_exact_metadata=False)
        job.labels.pop("metaproc-run-key")
        client = MagicMock()
        client.list_jobs.return_value = [job]

        with (
            patch("metaproc.commands.gcp._require_gcp_batch"),
            patch("google.cloud.batch_v1.BatchServiceClient", return_value=client),
        ):
            result = CliRunner().invoke(
                app,
                ["gcp", "runs", "--project", "p", "--json"],
            )

        assert result.exit_code == 0, result.output
        assert set(json.loads(result.output)) == {"legacy-run-id"}

    def test_modern_and_legacy_groups_with_same_display_are_not_combined(self) -> None:
        modern = self._job("run-abc", "modern")
        legacy = self._job("run-abc", "legacy", include_exact_metadata=False)
        legacy.labels.pop("metaproc-run-key")
        client = MagicMock()
        client.list_jobs.return_value = [legacy, modern]

        with (
            patch("metaproc.commands.gcp._require_gcp_batch"),
            patch("google.cloud.batch_v1.BatchServiceClient", return_value=client),
        ):
            result = CliRunner().invoke(
                app,
                ["gcp", "runs", "--project", "p", "--json"],
            )

        assert result.exit_code == 0, result.output
        inventory = json.loads(result.output)
        assert set(inventory) == {"run-abc", "run-abc [legacy]"}
        assert inventory["run-abc"][0]["job_id"] == "modern"
        assert inventory["run-abc [legacy]"][0]["job_id"] == "legacy"

    def test_structured_run_id_must_match_the_identity_hash(self) -> None:
        identity_key = run_identity_label("run-other")
        job = self._job("run_abc", "mismatch", identity_key=identity_key)
        client = MagicMock()
        client.list_jobs.return_value = [job]

        with (
            patch("metaproc.commands.gcp._require_gcp_batch"),
            patch("google.cloud.batch_v1.BatchServiceClient", return_value=client),
        ):
            result = CliRunner().invoke(
                app,
                ["gcp", "runs", "--project", "p", "--json"],
            )

        assert result.exit_code == 0, result.output
        assert set(json.loads(result.output)) == {f"run-abc [{identity_key}]"}

    def test_local_status_displays_exact_run_directory_identity(self, tmp_path: Path) -> None:
        run_id = "run-20260803T010203Z.1234560000.abc123def4"
        run_dir = tmp_path / run_id
        events_file = runpool_events(run_dir)
        events_file.parent.mkdir(parents=True)
        events_file.write_text(
            json.dumps(
                {
                    "event": "process_start",
                    "external_id": "projects/p/locations/r/jobs/exact-run",
                }
            )
        )
        job = self._job(run_id, "exact-run")
        client = MagicMock()
        client.get_job.return_value = job

        with (
            patch("metaproc.commands.gcp._require_gcp_batch"),
            patch("google.cloud.batch_v1.BatchServiceClient", return_value=client),
        ):
            result = CliRunner().invoke(app, ["gcp", "status", str(run_dir)])

        assert result.exit_code == 0, result.output
        assert f"Run: {run_id}" in result.output
        assert f"Run: {sanitize_label(run_id)}" not in result.output


class TestResolveScaleRunDir:
    def test_run_id_resolves_single_process_dir_from_runs_dir(self, tmp_path: Path) -> None:

        runs_root = tmp_path / "runs"
        run_dir = runs_root / "mine-run-1" / "mine"
        run_dir.mkdir(parents=True)
        (run_dir / STATE_DIR).mkdir(exist_ok=True)
        (run_dir / STATE_DIR / RUN_CONFIG_FILE).write_text("process: mine\n")

        with patch.dict("os.environ", {"RUNS_DIR": str(runs_root)}):
            resolved = _resolve_scale_run_dir("mine-run-1", step="generate-record")

        assert resolved == run_dir.resolve()


class TestGcpLogs:
    def test_local_run_dir_filters_by_job_ids(self, tmp_path: Path) -> None:

        events_file = runpool_events(tmp_path)
        events_file.parent.mkdir(parents=True)
        events = [
            {
                "event": "process_start",
                "external_id": "projects/myproj/locations/us-central1/jobs/j1",
                "label": "EVENT_ID=AAPL",
            },
            {
                "event": "process_start",
                "external_id": "projects/myproj/locations/us-central1/jobs/j2",
                "label": "EVENT_ID=MSFT",
            },
        ]
        events_file.write_text("\n".join(json.dumps(e) for e in events))

        runner = CliRunner()
        mock_client = MagicMock()
        mock_client.list_entries.return_value = []
        with (
            patch("metaproc.commands.gcp._require_gcp_batch"),
            patch("google.cloud.logging.Client", return_value=mock_client),
            patch(
                "metaproc.commands.gcp._resolve_job_uids",
                return_value=["uid-j1", "uid-j2"],
            ),
        ):
            result = runner.invoke(app, ["gcp", "logs", str(tmp_path)])

        assert result.exit_code == 0
        filter_str = mock_client.list_entries.call_args.kwargs["filter_"]
        assert 'labels."metaproc-run-id"' not in filter_str
        assert 'labels."job_uid"="uid-j1"' in filter_str
        assert 'labels."job_uid"="uid-j2"' in filter_str

    def test_local_run_dir_item_filter_limits_jobs(self, tmp_path: Path) -> None:

        events_file = runpool_events(tmp_path)
        events_file.parent.mkdir(parents=True)
        events = [
            {
                "event": "process_start",
                "external_id": "projects/myproj/locations/us-central1/jobs/j1",
                "label": "EVENT_ID=AAPL",
            },
            {
                "event": "process_start",
                "external_id": "projects/myproj/locations/us-central1/jobs/j2",
                "label": "EVENT_ID=MSFT",
            },
        ]
        events_file.write_text("\n".join(json.dumps(e) for e in events))

        runner = CliRunner()
        mock_client = MagicMock()
        mock_client.list_entries.return_value = []
        with (
            patch("metaproc.commands.gcp._require_gcp_batch"),
            patch("google.cloud.logging.Client", return_value=mock_client),
            patch(
                "metaproc.commands.gcp._resolve_job_uids",
                return_value=["uid-j1"],
            ),
        ):
            result = runner.invoke(app, ["gcp", "logs", str(tmp_path), "--item", "AAPL"])

        assert result.exit_code == 0
        filter_str = mock_client.list_entries.call_args.kwargs["filter_"]
        assert 'labels."job_uid"="uid-j1"' in filter_str
        assert 'labels."job_uid"="uid-j2"' not in filter_str

    def test_run_id_mode_defaults_to_task_logs_only(self) -> None:
        """Default filter excludes VM agent heartbeats (Phase 1 of
        plan-2026-04-20-metaproc-status-logs-unification.md).
        """

        runner = CliRunner()
        mock_client = MagicMock()
        mock_client.list_entries.return_value = []

        job = MagicMock()
        job.name = "projects/test-proj/locations/us-central1/jobs/j1"
        job.uid = "job-uid-123"
        job.labels = {"metaproc-role": "worker", "metaproc-worker-id": "0"}

        with (
            patch("metaproc.commands.gcp._require_gcp_batch"),
            patch("google.cloud.logging.Client", return_value=mock_client),
            patch("metaproc.commands.gcp._query_jobs_by_run_id", return_value=[job]),
            patch.dict("os.environ", {"METAPROC_GCP_PROJECT": "test-proj"}),
        ):
            result = runner.invoke(app, ["gcp", "logs", "mine-2026-04-09"])

        assert result.exit_code == 0
        filter_str = mock_client.list_entries.call_args.kwargs["filter_"]
        assert 'logName:"batch_task_logs"' in filter_str
        assert 'labels."job_uid"="job-uid-123"' in filter_str
        assert "batch_agent_logs" not in filter_str

    def test_run_id_mode_include_agent_logs_opt_in(self) -> None:
        """--include-agent-logs re-enables VM agent logs for bootstrap debugging."""

        runner = CliRunner()
        mock_client = MagicMock()
        mock_client.list_entries.return_value = []

        job = MagicMock()
        job.name = "projects/test-proj/locations/us-central1/jobs/j1"
        job.uid = "job-uid-123"
        job.labels = {"metaproc-role": "worker", "metaproc-worker-id": "0"}

        with (
            patch("metaproc.commands.gcp._require_gcp_batch"),
            patch("google.cloud.logging.Client", return_value=mock_client),
            patch("metaproc.commands.gcp._query_jobs_by_run_id", return_value=[job]),
            patch.dict("os.environ", {"METAPROC_GCP_PROJECT": "test-proj"}),
        ):
            result = runner.invoke(app, ["gcp", "logs", "mine-2026-04-09", "--include-agent-logs"])

        assert result.exit_code == 0
        filter_str = mock_client.list_entries.call_args.kwargs["filter_"]
        assert 'logName:"batch_task_logs"' in filter_str
        assert "logs/batch_agent_logs" in filter_str

    def test_run_id_mode_role_filter_restricts_job_uids(self) -> None:
        """--role worker only includes UIDs from worker jobs."""

        runner = CliRunner()
        mock_client = MagicMock()
        mock_client.list_entries.return_value = []

        orch = MagicMock()
        orch.name = "projects/test-proj/locations/us-central1/jobs/orch"
        orch.uid = "uid-orch"
        orch.labels = {"metaproc-role": "orchestrator"}

        w0 = MagicMock()
        w0.name = "projects/test-proj/locations/us-central1/jobs/w0"
        w0.uid = "uid-w0"
        w0.labels = {"metaproc-role": "worker", "metaproc-worker-id": "0"}

        w1 = MagicMock()
        w1.name = "projects/test-proj/locations/us-central1/jobs/w1"
        w1.uid = "uid-w1"
        w1.labels = {"metaproc-role": "worker", "metaproc-worker-id": "1"}

        with (
            patch("metaproc.commands.gcp._require_gcp_batch"),
            patch("google.cloud.logging.Client", return_value=mock_client),
            patch(
                "metaproc.commands.gcp._query_jobs_by_run_id",
                return_value=[orch, w0, w1],
            ),
            patch.dict("os.environ", {"METAPROC_GCP_PROJECT": "test-proj"}),
        ):
            result = runner.invoke(app, ["gcp", "logs", "mine-2026-04-09", "--role", "worker"])

        assert result.exit_code == 0
        filter_str = mock_client.list_entries.call_args.kwargs["filter_"]
        assert "uid-w0" in filter_str
        assert "uid-w1" in filter_str
        assert "uid-orch" not in filter_str

    def test_run_id_mode_worker_selector_pins_to_one_index(self) -> None:
        """--worker N restricts further to a single worker index."""

        runner = CliRunner()
        mock_client = MagicMock()
        mock_client.list_entries.return_value = []

        w0 = MagicMock()
        w0.name = "projects/test-proj/locations/us-central1/jobs/w0"
        w0.uid = "uid-w0"
        w0.labels = {"metaproc-role": "worker", "metaproc-worker-id": "0"}

        w1 = MagicMock()
        w1.name = "projects/test-proj/locations/us-central1/jobs/w1"
        w1.uid = "uid-w1"
        w1.labels = {"metaproc-role": "worker", "metaproc-worker-id": "1"}

        with (
            patch("metaproc.commands.gcp._require_gcp_batch"),
            patch("google.cloud.logging.Client", return_value=mock_client),
            patch(
                "metaproc.commands.gcp._query_jobs_by_run_id",
                return_value=[w0, w1],
            ),
            patch.dict("os.environ", {"METAPROC_GCP_PROJECT": "test-proj"}),
        ):
            result = runner.invoke(
                app,
                [
                    "gcp",
                    "logs",
                    "mine-2026-04-09",
                    "--role",
                    "worker",
                    "--worker",
                    "0",
                ],
            )

        assert result.exit_code == 0
        filter_str = mock_client.list_entries.call_args.kwargs["filter_"]
        assert "uid-w0" in filter_str
        assert "uid-w1" not in filter_str

    def test_worker_without_role_worker_is_rejected(self) -> None:

        runner = CliRunner()
        with (
            patch("metaproc.commands.gcp._require_gcp_batch"),
            patch.dict("os.environ", {"METAPROC_GCP_PROJECT": "test-proj"}),
        ):
            result = runner.invoke(app, ["gcp", "logs", "mine-2026-04-09", "--worker", "0"])

        assert result.exit_code != 0
        assert isinstance(result.exception, CLIError)
        assert "--worker requires --role worker" in str(result.exception)

    def test_invalid_role_is_rejected(self) -> None:

        runner = CliRunner()
        with (
            patch("metaproc.commands.gcp._require_gcp_batch"),
            patch.dict("os.environ", {"METAPROC_GCP_PROJECT": "test-proj"}),
        ):
            result = runner.invoke(app, ["gcp", "logs", "mine-2026-04-09", "--role", "bogus"])

        assert result.exit_code != 0

    def test_build_logs_filter_default_shape(self) -> None:

        flt = _build_logs_filter(job_uids=["u1", "u2"], project="proj-x")
        assert 'logName:"batch_task_logs"' in flt
        assert 'labels."job_uid"="u1"' in flt
        assert 'labels."job_uid"="u2"' in flt
        assert "batch_agent_logs" not in flt
        assert "severity" not in flt
        assert "timestamp" not in flt

    def test_build_logs_filter_with_include_agent_logs(self) -> None:

        flt = _build_logs_filter(job_uids=["u1"], project="proj-x", include_agent_logs=True)
        assert 'logName:"batch_task_logs"' in flt
        assert 'logName="projects/proj-x/logs/batch_agent_logs"' in flt

    def test_build_logs_filter_with_since_timestamp(self) -> None:

        flt = _build_logs_filter(
            job_uids=["u1"],
            project="proj-x",
            since_timestamp="2026-04-24T10:00:00Z",
        )
        assert 'timestamp>="2026-04-24T10:00:00Z"' in flt


class TestGcpScale:
    def test_scale_writes_desired_state_and_live_override(self, tmp_path: Path) -> None:

        run_dir = tmp_path / "mine-run" / "mine"
        worker_dir = worker_state_dir(run_dir, "worker-0")
        worker_dir.mkdir(parents=True, exist_ok=True)

        runner = CliRunner()

        async def fake_reconcile(**kwargs):
            return []

        with (
            patch(
                "metaproc.commands.gcp._load_scale_reconcile_context",
                return_value=(
                    tmp_path / "process",
                    {"RUN_ID": "mine-run"},
                    [{"EVENT_ID": "AAPL"}],
                    "EVENT_ID",
                    "pi-glm-5",
                ),
            ),
            patch(
                "metaproc.cloud.gcp.worker_dispatch.build_gcp_config_from_env",
                return_value=MagicMock(filestore_server="10.0.0.1"),
            ),
            patch(
                "metaproc.cloud.gcp.worker_dispatch.reconcile_dispatched_workers",
                side_effect=fake_reconcile,
            ),
        ):
            result = runner.invoke(
                app,
                [
                    "gcp",
                    "scale",
                    str(run_dir),
                    "--step",
                    "generate-record",
                    "--num-workers",
                    "2",
                    "--max-concurrency",
                    "15",
                    "--yes",
                ],
            )

        assert result.exit_code == 0

        # New layout: <run>/.state/steps/<step_id>/scale-state.yaml
        scale_state = read_scale_state(
            step_state_dir(run_dir, "generate-record") / SCALE_STATE_FILE
        )
        assert scale_state.desired_workers == 2
        assert scale_state.desired_max_concurrency == 15
        assert scale_state.generation == 1

        override = read_scale_override(worker_dir / SCALE_OVERRIDE_FILE)
        assert override.operator_cap == 15

    def test_scale_reconcile_infers_non_spot_from_existing_worker(self, tmp_path: Path) -> None:

        run_dir = tmp_path / "mine-run" / "mine"
        worker_state_dir(run_dir, "worker-0").mkdir(parents=True, exist_ok=True)

        runner = CliRunner()
        captured: dict[str, object] = {}

        async def fake_reconcile(**kwargs):
            captured["config"] = kwargs["config"]
            return []

        gcp_config = MagicMock(filestore_server="10.0.0.1")

        with (
            patch.dict("os.environ", {"METAPROC_GCP_PROJECT": "exampletool"}, clear=False),
            patch(
                "metaproc.commands.gcp._load_scale_reconcile_context",
                return_value=(
                    tmp_path / "process",
                    {"RUN_ID": "mine-run"},
                    [{"EVENT_ID": "AAPL"}],
                    "EVENT_ID",
                    "pi-glm-5",
                ),
            ),
            patch(
                "metaproc.commands.gcp._infer_scale_spot_from_manifest",
                return_value=False,
            ),
            patch(
                "metaproc.cloud.gcp.worker_dispatch.build_gcp_config_from_env",
                return_value=gcp_config,
            ) as mock_build_config,
            patch(
                "metaproc.cloud.gcp.worker_dispatch.reconcile_dispatched_workers",
                side_effect=fake_reconcile,
            ),
        ):
            result = runner.invoke(
                app,
                [
                    "gcp",
                    "scale",
                    str(run_dir),
                    "--step",
                    "generate-record",
                    "--num-workers",
                    "2",
                    "--yes",
                ],
            )

        assert result.exit_code == 0
        mock_build_config.assert_called_once_with(spot=False)
        config = captured["config"]
        assert config.spot is False
        assert config.gcp is gcp_config


class TestScaleSpotInference:
    def test_reads_spot_from_dispatched_worker_job(self, tmp_path: Path) -> None:

        run_dir = tmp_path / "mine-run" / "mine"
        write_dispatch_manifest(
            run_dir,
            "generate-record",
            worker_jobs=[
                {
                    "worker_id": "0",
                    "job_name": "projects/p/locations/us-central1/jobs/j1",
                    "job_id": "j1",
                    "items_count": "1",
                    "items": ["AAPL"],
                }
            ],
            num_items=1,
            variant="pi-glm-5",
        )

        job = MagicMock()
        job.allocation_policy.instances = [
            MagicMock(
                policy=MagicMock(provisioning_model=AllocationPolicy.ProvisioningModel.STANDARD)
            )
        ]
        client = MagicMock()
        client.get_job.return_value = job

        with patch("google.cloud.batch_v1.BatchServiceClient", return_value=client):
            assert (
                _infer_scale_spot_from_manifest(
                    run_dir,
                    step="generate-record",
                )
                is False
            )


# ── CLI integration (help text) ─────────────────────────────────


class TestGCPCLIHelp:
    def test_status_help(self):

        runner = CliRunner()
        result = runner.invoke(app, ["gcp", "status", "--help"])
        assert result.exit_code == 0
        assert "target" in result.output.lower()
        assert "run directory or run-id" in result.output.lower()

    def test_logs_help(self):

        runner = CliRunner()
        result = runner.invoke(app, ["gcp", "logs", "--help"])
        assert result.exit_code == 0
        assert "target" in result.output.lower()

    def test_scale_help(self):

        # Rich/typer's `--help` truncates long flag names with an ellipsis when
        # CliRunner's fake terminal is narrow (seen intermittently on CI even
        # with COLUMNS=200), so introspect the click command tree directly
        # instead of asserting on rendered help text.
        click_app = get_command(app)
        scale_cmd = click_app.commands["gcp"].commands["scale"]
        flag_names = {opt for param in scale_cmd.params for opt in param.opts}
        assert "--num-workers" in flag_names
        assert "--max-concurrency" in flag_names

    def test_cancel_help(self):

        runner = CliRunner()
        result = runner.invoke(app, ["gcp", "cancel", "--help"])
        assert result.exit_code == 0
        assert "target" in result.output.lower()

    def test_runs_help(self):

        runner = CliRunner()
        result = runner.invoke(app, ["gcp", "runs", "--help"])
        assert result.exit_code == 0
        assert "active" in result.output.lower() or "runs" in result.output.lower()

    def test_no_cloud_status_command(self):

        runner = CliRunner()
        result = runner.invoke(app, ["gcp", "cloud-status", "--help"])
        assert result.exit_code != 0

    def test_no_build_image_command(self):

        runner = CliRunner()
        result = runner.invoke(app, ["gcp", "build-image", "--help"])
        assert result.exit_code != 0

    def test_no_check_image_command(self):

        runner = CliRunner()
        result = runner.invoke(app, ["gcp", "check-image", "--help"])
        assert result.exit_code != 0

    def test_resources_help(self):

        runner = CliRunner()
        result = runner.invoke(app, ["gcp", "resources", "--help"])
        assert result.exit_code == 0
        assert "asset inventory" in result.output.lower() or "snapshot" in result.output.lower()


# ── Resources command ──────────────────────────────────────────


class TestSearchResources:
    def _make_resource(self, name: str, asset_type: str, location: str = "us-central1"):
        r = MagicMock()
        r.name = name
        r.location = location
        r.state = "READY"
        r.labels = {"env": "prod"}
        r.update_time.isoformat.return_value = "2026-04-09T00:00:00Z"
        r.asset_type = asset_type
        return r

    def test_groups_by_asset_type(self):
        resources = [
            self._make_resource(
                "projects/p/locations/us-central1/instances/metaproc-filestore",
                "file.googleapis.com/Instance",
            ),
            self._make_resource(
                "projects/p/buckets/metaproc-runs",
                "storage.googleapis.com/Bucket",
                location="us",
            ),
        ]

        def fake_search(request):
            at = request.asset_types[0]
            return [r for r in resources if r.asset_type == at]

        mock_client = MagicMock()
        mock_client.search_all_resources.side_effect = fake_search

        with patch("google.cloud.asset_v1.AssetServiceClient", return_value=mock_client):
            result = _search_resources("test-project")

        assert len(result) == 2
        assert result[0]["asset_type"] == "file.googleapis.com/Instance"
        assert result[0]["name"] == "metaproc-filestore"
        assert result[1]["asset_type"] == "storage.googleapis.com/Bucket"
        assert result[1]["name"] == "metaproc-runs"

    def test_handles_api_error_gracefully(self):
        mock_client = MagicMock()
        mock_client.search_all_resources.side_effect = Exception("permission denied")

        with patch("google.cloud.asset_v1.AssetServiceClient", return_value=mock_client):
            result = _search_resources("test-project")

        # Should have one error entry per asset type.
        assert len(result) == len(_ASSET_TYPES)
        assert "<error" in str(result[0]["name"])

    def test_empty_project(self):
        mock_client = MagicMock()
        mock_client.search_all_resources.return_value = []

        with patch("google.cloud.asset_v1.AssetServiceClient", return_value=mock_client):
            result = _search_resources("empty-project")

        assert result == []


class TestResourcesCLI:
    def test_requires_project(self):

        runner = CliRunner()
        with patch.dict("os.environ", {}, clear=True):
            result = runner.invoke(app, ["gcp", "resources"])
        assert result.exit_code != 0

    def test_json_output(self):

        mock_resources = [
            {
                "asset_type": "file.googleapis.com/Instance",
                "name": "metaproc-filestore",
                "full_name": "projects/p/locations/r/instances/metaproc-filestore",
                "location": "us-central1",
                "state": "READY",
                "labels": {},
                "update_time": "2026-04-09T00:00:00Z",
            }
        ]

        runner = CliRunner()
        with (
            patch("metaproc.commands.gcp._search_resources", return_value=mock_resources),
            patch("metaproc.commands.gcp._require_gcp_asset"),
            patch.dict("os.environ", {"METAPROC_GCP_PROJECT": "test-project"}),
        ):
            result = runner.invoke(app, ["gcp", "resources", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["name"] == "metaproc-filestore"

    def test_text_output_grouped(self):

        mock_resources = [
            {
                "asset_type": "file.googleapis.com/Instance",
                "name": "metaproc-filestore",
                "full_name": "projects/p/locations/r/instances/metaproc-filestore",
                "location": "us-central1",
                "state": "READY",
                "labels": {},
                "update_time": "",
            },
            {
                "asset_type": "storage.googleapis.com/Bucket",
                "name": "metaproc-runs",
                "full_name": "projects/p/buckets/metaproc-runs",
                "location": "us",
                "state": "",
                "labels": {},
                "update_time": "",
            },
        ]

        runner = CliRunner()
        with (
            patch("metaproc.commands.gcp._search_resources", return_value=mock_resources),
            patch("metaproc.commands.gcp._require_gcp_asset"),
            patch.dict("os.environ", {"METAPROC_GCP_PROJECT": "test-project"}),
        ):
            result = runner.invoke(app, ["gcp", "resources"])
        assert result.exit_code == 0
        assert "Filestore Instances" in result.output
        assert "GCS Buckets" in result.output
        assert "metaproc-filestore" in result.output
        assert "metaproc-runs" in result.output


# ── Filestore command ──────────────────────────────────────────


class TestListFilestoreInstances:
    def _make_instance(self, name: str, state: int = 1, tier: int = 1):
        inst = MagicMock()
        inst.name = name
        inst.state = state  # 1 = CREATING, 2 = READY in the enum
        inst.tier = tier  # 1 = STANDARD

        share = MagicMock()
        share.name = "metaproc_share"
        share.capacity_gb = 1024
        inst.file_shares = [share]

        network = MagicMock()
        network.ip_addresses = ["10.0.0.1"]
        inst.networks = [network]

        return inst

    def test_lists_metaproc_instances(self):

        instances = [
            self._make_instance(
                "projects/p/locations/us-central1/instances/metaproc-nfs",
                state=filestore_v1.Instance.State.READY,
                tier=filestore_v1.Instance.Tier.BASIC_HDD,
            ),
            self._make_instance(
                "projects/p/locations/us-central1/instances/other-nfs",
                state=filestore_v1.Instance.State.READY,
            ),
        ]

        mock_client = MagicMock()
        mock_client.list_instances.return_value = instances

        with patch(
            "google.cloud.filestore_v1.CloudFilestoreManagerClient",
            return_value=mock_client,
        ):
            result = _list_filestore_instances("test-project", "us-central1")

        # Only metaproc instance returned.
        assert len(result) == 1
        assert result[0]["name"] == "metaproc-nfs"
        assert result[0]["capacity_gb"] == 1024
        assert result[0]["ip"] == "10.0.0.1"
        assert result[0]["share_name"] == "metaproc_share"

    def test_empty_region(self):
        mock_client = MagicMock()
        mock_client.list_instances.return_value = []

        with patch(
            "google.cloud.filestore_v1.CloudFilestoreManagerClient",
            return_value=mock_client,
        ):
            result = _list_filestore_instances("test-project", "us-central1")

        assert result == []


class TestQueryFilestoreUtilization:
    def test_returns_utilization(self):
        point = MagicMock()
        point.value.double_value = 42.5

        ts = MagicMock()
        ts.points = [point]

        mock_client = MagicMock()
        mock_client.list_time_series.return_value = [ts]

        with patch(
            "google.cloud.monitoring_v3.MetricServiceClient",
            return_value=mock_client,
        ):
            result = _query_filestore_utilization("test-project", "metaproc-nfs")

        assert result == 42.5

    def test_returns_none_on_no_data(self):
        mock_client = MagicMock()
        mock_client.list_time_series.return_value = []

        with patch(
            "google.cloud.monitoring_v3.MetricServiceClient",
            return_value=mock_client,
        ):
            result = _query_filestore_utilization("test-project", "metaproc-nfs")

        assert result is None

    def test_returns_none_on_error(self):
        mock_client = MagicMock()
        mock_client.list_time_series.side_effect = Exception("auth error")

        with patch(
            "google.cloud.monitoring_v3.MetricServiceClient",
            return_value=mock_client,
        ):
            result = _query_filestore_utilization("test-project", "metaproc-nfs")

        assert result is None


class TestFilestoreCLI:
    def test_help(self):

        runner = CliRunner()
        result = runner.invoke(app, ["gcp", "filestore", "--help"])
        assert result.exit_code == 0
        assert "filestore" in result.output.lower()

    def test_requires_project(self):

        runner = CliRunner()
        with patch.dict("os.environ", {}, clear=True):
            result = runner.invoke(app, ["gcp", "filestore"])
        assert result.exit_code != 0

    def test_json_output(self):

        mock_instances = [
            {
                "name": "metaproc-nfs",
                "full_name": "projects/p/locations/r/instances/metaproc-nfs",
                "state": "READY",
                "tier": "BASIC_HDD",
                "capacity_gb": 1024,
                "share_name": "metaproc_share",
                "ip": "10.0.0.1",
                "used_pct": 42.5,
            }
        ]

        runner = CliRunner()
        with (
            patch("metaproc.commands.gcp._list_filestore_instances", return_value=mock_instances),
            patch("metaproc.commands.gcp._query_filestore_utilization", return_value=42.5),
            patch("metaproc.commands.gcp._require_gcp_filestore"),
            patch.dict("os.environ", {"METAPROC_GCP_PROJECT": "test-project"}),
        ):
            result = runner.invoke(app, ["gcp", "filestore", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["name"] == "metaproc-nfs"

    def test_text_output(self):

        mock_instances = [
            {
                "name": "metaproc-nfs",
                "full_name": "projects/p/locations/r/instances/metaproc-nfs",
                "state": "READY",
                "tier": "BASIC_HDD",
                "capacity_gb": 1024,
                "share_name": "metaproc_share",
                "ip": "10.0.0.1",
            }
        ]

        runner = CliRunner()
        with (
            patch("metaproc.commands.gcp._list_filestore_instances", return_value=mock_instances),
            patch("metaproc.commands.gcp._query_filestore_utilization", return_value=55.3),
            patch("metaproc.commands.gcp._require_gcp_filestore"),
            patch.dict("os.environ", {"METAPROC_GCP_PROJECT": "test-project"}),
        ):
            result = runner.invoke(app, ["gcp", "filestore"])
        assert result.exit_code == 0
        assert "metaproc-nfs" in result.output
        assert "READY" in result.output
        assert "1024" in result.output
        assert "55.3%" in result.output


# ── Archive command tests ────────────────────────────────────────


class TestArchiveCLI:
    def test_archive_help(self) -> None:

        runner = CliRunner()
        result = runner.invoke(app, ["gcp", "archive", "--help"])
        assert result.exit_code == 0
        assert "run directory" in result.output.lower() or "archive" in result.output.lower()

    def test_archive_nonexistent_dir(self, tmp_path: Path) -> None:

        runner = CliRunner()
        fake_dir = str(tmp_path / "no-such-dir")
        with patch("metaproc.commands.gcp._require_gcp_storage"):
            result = runner.invoke(app, ["gcp", "archive", fake_dir, "--yes"])
        assert result.exit_code == 1
        assert "Not a directory" in result.output

    def test_archive_runs_gsutil(self, tmp_path: Path) -> None:

        runner = CliRunner()
        run_dir = tmp_path / "test-run"
        run_dir.mkdir()
        (run_dir / "output.txt").write_text("data")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with (
            patch("metaproc.commands.gcp._require_gcp_storage"),
            patch("subprocess.run", return_value=mock_result) as mock_run,
        ):
            result = runner.invoke(
                app,
                ["gcp", "archive", str(run_dir), "--bucket", "my-bucket", "--yes"],
            )
        assert result.exit_code == 0
        assert "Archived" in result.output
        # Verify gsutil was called with correct args.
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "gsutil"
        assert "my-bucket" in cmd[-1]

    def test_archive_delete_local(self, tmp_path: Path) -> None:

        runner = CliRunner()
        run_dir = tmp_path / "to-delete"
        run_dir.mkdir()
        (run_dir / "file.txt").write_text("content")

        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch("metaproc.commands.gcp._require_gcp_storage"),
            patch("subprocess.run", return_value=mock_result),
        ):
            result = runner.invoke(
                app,
                [
                    "gcp",
                    "archive",
                    str(run_dir),
                    "--bucket",
                    "test-bucket",
                    "--delete-local",
                    "--yes",
                ],
            )
        assert result.exit_code == 0
        assert "Deleted local" in result.output
        assert not run_dir.exists()

    def test_archive_gsutil_failure(self, tmp_path: Path) -> None:

        runner = CliRunner()
        run_dir = tmp_path / "fail-run"
        run_dir.mkdir()
        (run_dir / "f.txt").write_text("x")

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "AccessDenied"

        with (
            patch("metaproc.commands.gcp._require_gcp_storage"),
            patch("subprocess.run", return_value=mock_result),
        ):
            result = runner.invoke(
                app,
                ["gcp", "archive", str(run_dir), "--bucket", "test-bucket", "--yes"],
            )
        assert result.exit_code == 1


# ── Cleanup command tests ────────────────────────────────────────


class TestCleanupCLI:
    def test_cleanup_help(self) -> None:

        runner = CliRunner()
        result = runner.invoke(app, ["gcp", "cleanup", "--help"])
        assert result.exit_code == 0
        assert "older" in result.output.lower() or "cleanup" in result.output.lower()

    def test_cleanup_requires_project(self, monkeypatch: pytest.MonkeyPatch) -> None:

        monkeypatch.delenv("METAPROC_GCP_PROJECT", raising=False)
        runner = CliRunner()
        with patch("metaproc.commands.gcp._require_gcp_batch"):
            result = runner.invoke(app, ["gcp", "cleanup", "--yes"])
        assert result.exit_code == 1
        assert "project" in result.output.lower()

    def test_cleanup_no_old_jobs(self) -> None:

        runner = CliRunner()
        mock_client = MagicMock()
        mock_job = MagicMock()
        mock_job.status.state = 4  # SUCCEEDED
        mock_job.create_time = datetime.now(tz=UTC)
        mock_job.name = "projects/p/locations/r/jobs/recent-job"
        mock_client.list_jobs.return_value = [mock_job]

        mock_batch = MagicMock()
        mock_batch.BatchServiceClient.return_value = mock_client
        mock_batch.ListJobsRequest = MagicMock()

        mock_job_status = MagicMock()
        mock_job_status.State.SUCCEEDED = 4
        mock_job_status.State.FAILED = 5

        # Mock the entire google.cloud.batch_v1 import chain so deferred imports work
        mock_types_job = MagicMock()
        mock_types_job.JobStatus = mock_job_status
        mock_types = MagicMock()
        mock_types.job = mock_types_job
        mock_batch.types = mock_types
        mock_batch.types.job = mock_types_job

        sys_mods = {
            "google.cloud.batch_v1": mock_batch,
            "google.cloud.batch_v1.types": mock_types,
            "google.cloud.batch_v1.types.job": mock_types_job,
        }
        with (
            patch("metaproc.commands.gcp._require_gcp_batch"),
            patch.dict("sys.modules", sys_mods),
            patch.dict("os.environ", {"METAPROC_GCP_PROJECT": "test-proj"}),
        ):
            # Also patch the parent package attribute so cached imports don't bypass

            gc = sys.modules.get("google.cloud")
            _orig_batch_v1 = getattr(gc, "batch_v1", None) if gc is not None else None
            if gc is not None:
                gc.batch_v1 = mock_batch  # type: ignore[attr-defined]
            try:
                result = runner.invoke(app, ["gcp", "cleanup", "--yes"])
            finally:
                if gc is not None:
                    if _orig_batch_v1 is not None:
                        gc.batch_v1 = _orig_batch_v1  # type: ignore[attr-defined]
                    elif hasattr(gc, "batch_v1"):
                        del gc.batch_v1  # type: ignore[attr-defined]
        assert result.exit_code == 0
        assert "No Batch jobs" in result.output

    def test_cleanup_deletes_old_jobs(self) -> None:

        runner = CliRunner()
        mock_client = MagicMock()

        old_time = datetime.now(tz=UTC) - timedelta(days=45)
        mock_job = MagicMock()
        mock_job.status.state = 4  # SUCCEEDED
        mock_job.create_time = old_time
        mock_job.name = "projects/p/locations/r/jobs/old-job-1"
        mock_client.list_jobs.return_value = [mock_job]
        mock_client.delete_job.return_value = None

        mock_batch = MagicMock()
        mock_batch.BatchServiceClient.return_value = mock_client
        mock_batch.ListJobsRequest = MagicMock()
        mock_batch.DeleteJobRequest = MagicMock()

        mock_job_status = MagicMock()
        mock_job_status.State.SUCCEEDED = 4
        mock_job_status.State.FAILED = 5
        mock_job_status.State.return_value.name = "SUCCEEDED"

        mock_types_job = MagicMock()
        mock_types_job.JobStatus = mock_job_status
        mock_types = MagicMock()
        mock_types.job = mock_types_job
        mock_batch.types = mock_types
        mock_batch.types.job = mock_types_job

        sys_mods = {
            "google.cloud.batch_v1": mock_batch,
            "google.cloud.batch_v1.types": mock_types,
            "google.cloud.batch_v1.types.job": mock_types_job,
        }
        with (
            patch("metaproc.commands.gcp._require_gcp_batch"),
            patch.dict("sys.modules", sys_mods),
            patch.dict("os.environ", {"METAPROC_GCP_PROJECT": "test-proj"}),
        ):
            gc = sys.modules.get("google.cloud")
            _orig_batch_v1 = getattr(gc, "batch_v1", None) if gc is not None else None
            if gc is not None:
                gc.batch_v1 = mock_batch  # type: ignore[attr-defined]
            try:
                result = runner.invoke(app, ["gcp", "cleanup", "--yes"])
            finally:
                if gc is not None:
                    if _orig_batch_v1 is not None:
                        gc.batch_v1 = _orig_batch_v1  # type: ignore[attr-defined]
                    elif hasattr(gc, "batch_v1"):
                        del gc.batch_v1  # type: ignore[attr-defined]
        assert result.exit_code == 0
        assert "Deleted 1/1" in result.output
        mock_client.delete_job.assert_called_once()

    def test_cleanup_json_output(self) -> None:

        runner = CliRunner()
        mock_client = MagicMock()

        old_time = datetime.now(tz=UTC) - timedelta(days=60)
        mock_job = MagicMock()
        mock_job.status.state = 5  # FAILED
        mock_job.create_time = old_time
        mock_job.name = "projects/p/locations/r/jobs/failed-old"
        mock_client.list_jobs.return_value = [mock_job]
        mock_client.delete_job.return_value = None

        mock_batch = MagicMock()
        mock_batch.BatchServiceClient.return_value = mock_client
        mock_batch.ListJobsRequest = MagicMock()
        mock_batch.DeleteJobRequest = MagicMock()

        mock_job_status = MagicMock()
        mock_job_status.State.SUCCEEDED = 4
        mock_job_status.State.FAILED = 5
        mock_job_status.State.return_value.name = "FAILED"

        mock_types_job = MagicMock()
        mock_types_job.JobStatus = mock_job_status
        mock_types = MagicMock()
        mock_types.job = mock_types_job
        mock_batch.types = mock_types
        mock_batch.types.job = mock_types_job

        sys_mods = {
            "google.cloud.batch_v1": mock_batch,
            "google.cloud.batch_v1.types": mock_types,
            "google.cloud.batch_v1.types.job": mock_types_job,
        }
        with (
            patch("metaproc.commands.gcp._require_gcp_batch"),
            patch.dict("sys.modules", sys_mods),
            patch.dict("os.environ", {"METAPROC_GCP_PROJECT": "test-proj"}),
        ):
            gc = sys.modules.get("google.cloud")
            _orig_batch_v1 = getattr(gc, "batch_v1", None) if gc is not None else None
            if gc is not None:
                gc.batch_v1 = mock_batch  # type: ignore[attr-defined]
            try:
                result = runner.invoke(app, ["gcp", "cleanup", "--json", "--yes"])
            finally:
                if gc is not None:
                    if _orig_batch_v1 is not None:
                        gc.batch_v1 = _orig_batch_v1  # type: ignore[attr-defined]
                    elif hasattr(gc, "batch_v1"):
                        del gc.batch_v1  # type: ignore[attr-defined]
        assert result.exit_code == 0
        data = json.loads(result.output.split("Deleted")[0])
        assert len(data) == 1
        assert data[0]["name"] == "projects/p/locations/r/jobs/failed-old"


# ── _ensure_remote_repo tests ──────────────────────────────────


class TestEnsureRemoteRepo:
    """Tests for _ensure_remote_repo — mocks subprocess.run to verify SSH calls."""

    def test_skips_when_repo_exists_and_no_sync(self) -> None:
        """Default mode: skip if repo already cloned."""

        check_result = MagicMock(returncode=0)  # repo exists

        with (
            patch("subprocess.run", return_value=check_result) as mock_run,
            patch.dict("os.environ", {"METAPROC_REPO_URL": "https://github.com/org/repo.git"}),
        ):
            _ensure_remote_repo("myhost", zone="us-central1-b", project="myproj")

        # Only one call: the existence check.
        assert mock_run.call_count == 1
        cmd = mock_run.call_args_list[0][0][0]
        assert "test -d ~/metaproc-repo/.git" in " ".join(cmd)

    def test_syncs_when_repo_exists_and_sync_true(self) -> None:
        """--sync mode: pull + reinstall when repo already exists."""

        check_result = MagicMock(returncode=0)  # repo exists
        sync_result = MagicMock(returncode=0)

        with (
            patch("subprocess.run", side_effect=[check_result, sync_result]) as mock_run,
            patch.dict(
                "os.environ",
                {
                    "METAPROC_REPO_URL": "https://github.com/org/repo.git",
                    "METAPROC_RUN_BRANCH": "feat/test",
                },
            ),
        ):
            _ensure_remote_repo("myhost", zone="us-central1-b", project="myproj", sync=True)

        assert mock_run.call_count == 2
        sync_cmd = " ".join(mock_run.call_args_list[1][0][0])
        assert "git fetch origin" in sync_cmd
        assert "feat/test" in sync_cmd
        assert "uv pip install" in sync_cmd

    def test_clones_when_repo_does_not_exist(self) -> None:
        """Fresh clone when repo not found on remote host."""

        check_result = MagicMock(returncode=1)  # repo doesn't exist
        clone_result = MagicMock(returncode=0)

        with (
            patch("subprocess.run", side_effect=[check_result, clone_result]) as mock_run,
            patch.dict(
                "os.environ",
                {
                    "METAPROC_REPO_URL": "https://github.com/org/repo.git",
                    "METAPROC_RUN_BRANCH": "main",
                },
            ),
        ):
            _ensure_remote_repo("myhost", zone="us-central1-b", project="myproj")

        assert mock_run.call_count == 2
        clone_cmd = " ".join(mock_run.call_args_list[1][0][0])
        assert "git clone --depth 1" in clone_cmd
        assert "https://github.com/org/repo.git" in clone_cmd
        assert "~/metaproc-repo" in clone_cmd

    def test_raises_when_no_repo_url(self) -> None:
        """Exits with error if METAPROC_REPO_URL is not set."""

        with (
            patch.dict("os.environ", {}, clear=True),
            patch("subprocess.run"),
            pytest.raises(typer.Exit),
        ):
            _ensure_remote_repo("myhost", zone="z", project="p")

    def test_raises_on_clone_failure(self) -> None:
        """Exits if the clone command fails."""

        check_result = MagicMock(returncode=1)  # doesn't exist
        clone_result = MagicMock(returncode=128)  # git clone fails

        with (
            patch("subprocess.run", side_effect=[check_result, clone_result]),
            patch.dict("os.environ", {"METAPROC_REPO_URL": "https://github.com/org/repo.git"}),
            pytest.raises(typer.Exit),
        ):
            _ensure_remote_repo("myhost", zone="z", project="p")

    def test_uses_iap_tunneling(self) -> None:
        """All SSH calls should use --tunnel-through-iap."""

        check_result = MagicMock(returncode=1)
        clone_result = MagicMock(returncode=0)

        with (
            patch("subprocess.run", side_effect=[check_result, clone_result]) as mock_run,
            patch.dict("os.environ", {"METAPROC_REPO_URL": "https://github.com/org/repo.git"}),
        ):
            _ensure_remote_repo("myhost", zone="us-central1-b", project="myproj")

        for call in mock_run.call_args_list:
            cmd = call[0][0]
            assert "--tunnel-through-iap" in cmd, f"IAP missing from: {cmd}"


# ── gcp remote command tests ───────────────────────────────────


class TestGcpRemote:
    """Tests for the ``gcp remote`` CLI command — path expansion and SSH wiring."""

    def test_remote_help(self) -> None:

        runner = CliRunner()
        result = runner.invoke(app, ["gcp", "remote", "--help"])
        assert result.exit_code == 0
        assert "remote" in result.output.lower()

    def test_run_id_path_expansion(self) -> None:
        """--run-id + --phase expands to /mnt/filestore/runs/{run_id}/{phase}."""

        runner = CliRunner()
        ssh_result = MagicMock(returncode=0)

        with (
            patch("metaproc.commands.gcp._ensure_remote_repo"),
            patch("subprocess.run", return_value=ssh_result) as mock_run,
            patch.dict(
                "os.environ",
                {
                    "METAPROC_GCP_PROJECT": "test-proj",
                    "METAPROC_GATEWAY_HOST": "browser-vm",
                },
            ),
        ):
            result = runner.invoke(
                app,
                [
                    "gcp",
                    "remote",
                    "--run-id",
                    "cloud-500-ds",
                    "--phase",
                    "mine",
                    "stats",
                ],
            )

        assert result.exit_code == 0
        # Verify the SSH command includes the expanded path.
        ssh_cmd = mock_run.call_args[0][0]
        remote_cmd = ssh_cmd[-1]  # last element is the remote command string
        assert "/mnt/filestore/runs/cloud-500-ds/mine" in remote_cmd
        assert "metaproc" in remote_cmd
        assert "stats" in remote_cmd

    def test_default_phase_is_mine(self) -> None:
        """Default phase is 'mine' when --phase not specified."""

        runner = CliRunner()
        ssh_result = MagicMock(returncode=0)

        with (
            patch("metaproc.commands.gcp._ensure_remote_repo"),
            patch("subprocess.run", return_value=ssh_result) as mock_run,
            patch.dict(
                "os.environ",
                {
                    "METAPROC_GCP_PROJECT": "test-proj",
                    "METAPROC_GATEWAY_HOST": "browser-vm",
                },
            ),
        ):
            result = runner.invoke(
                app,
                ["gcp", "remote", "--run-id", "my-run", "status"],
            )

        assert result.exit_code == 0
        remote_cmd = mock_run.call_args[0][0][-1]
        assert "/mnt/filestore/runs/my-run/mine" in remote_cmd

    def test_no_run_id_passes_through(self) -> None:
        """Without --run-id, command is passed through without path expansion."""

        runner = CliRunner()
        ssh_result = MagicMock(returncode=0)

        with (
            patch("metaproc.commands.gcp._ensure_remote_repo"),
            patch("subprocess.run", return_value=ssh_result) as mock_run,
            patch.dict(
                "os.environ",
                {
                    "METAPROC_GCP_PROJECT": "test-proj",
                    "METAPROC_GATEWAY_HOST": "browser-vm",
                },
            ),
        ):
            result = runner.invoke(
                app,
                ["gcp", "remote", "gcp", "runs"],
            )

        assert result.exit_code == 0
        remote_cmd = mock_run.call_args[0][0][-1]
        assert "/mnt/filestore/runs" not in remote_cmd
        assert "metaproc" in remote_cmd
        assert "gcp" in remote_cmd
        assert "runs" in remote_cmd

    def test_requires_project(self) -> None:
        """Exits if no --project or METAPROC_GCP_PROJECT."""

        runner = CliRunner()
        with (
            patch.dict("os.environ", {"METAPROC_GATEWAY_HOST": "vm"}, clear=True),
        ):
            result = runner.invoke(app, ["gcp", "remote", "status"])
        assert result.exit_code != 0

    def test_requires_gateway_host(self) -> None:
        """A missing host fails with an actionable configuration error."""
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(CLIError, match="METAPROC_GATEWAY_HOST"):
                _resolve_gateway_host("")

    def test_raw_flag_skips_metaproc_prefix(self) -> None:
        """--raw passes command through without prepending 'metaproc'."""

        runner = CliRunner()
        ssh_result = MagicMock(returncode=0)

        with (
            patch("metaproc.commands.gcp._ensure_remote_repo"),
            patch("subprocess.run", return_value=ssh_result) as mock_run,
            patch.dict(
                "os.environ",
                {
                    "METAPROC_GCP_PROJECT": "test-proj",
                    "METAPROC_GATEWAY_HOST": "browser-vm",
                },
            ),
        ):
            result = runner.invoke(
                app,
                [
                    "gcp",
                    "remote",
                    "--raw",
                    "--",
                    "python",
                    "-m",
                    "example_plugin.compare_records",
                    "--bulk",
                ],
            )

        assert result.exit_code == 0
        remote_cmd = mock_run.call_args[0][0][-1]
        assert "python" in remote_cmd
        assert "example_plugin.compare_records" in remote_cmd
        assert "--bulk" in remote_cmd
        # Should NOT have 'metaproc' prefix (only in PATH setup)
        assert "metaproc python" not in remote_cmd

    def test_raw_flag_skips_repo_bootstrap_by_default(self) -> None:
        """Raw commands should not require a remote repo unless --sync is requested."""

        runner = CliRunner()
        ssh_result = MagicMock(returncode=0)

        with (
            patch("metaproc.commands.gcp._ensure_remote_repo") as ensure_repo,
            patch("subprocess.run", return_value=ssh_result),
            patch.dict(
                "os.environ",
                {
                    "METAPROC_GATEWAY_HOST": "browser-vm",
                    "METAPROC_GCP_PROJECT": "test-proj",
                },
                clear=True,
            ),
        ):
            result = runner.invoke(
                app,
                [
                    "gcp",
                    "remote",
                    "--raw",
                    "--",
                    "ls",
                    "/mnt/filestore/runs",
                ],
            )

        assert result.exit_code == 0
        ensure_repo.assert_not_called()

    def test_custom_phase(self) -> None:
        """--phase overrides the default 'mine'."""

        runner = CliRunner()
        ssh_result = MagicMock(returncode=0)

        with (
            patch("metaproc.commands.gcp._ensure_remote_repo"),
            patch("subprocess.run", return_value=ssh_result) as mock_run,
            patch.dict(
                "os.environ",
                {
                    "METAPROC_GCP_PROJECT": "test-proj",
                    "METAPROC_GATEWAY_HOST": "browser-vm",
                },
            ),
        ):
            result = runner.invoke(
                app,
                ["gcp", "remote", "--run-id", "run-1", "--phase", "qa", "stats"],
            )

        assert result.exit_code == 0
        remote_cmd = mock_run.call_args[0][0][-1]
        assert "/mnt/filestore/runs/run-1/qa" in remote_cmd


# ── remote-run tests ─────────────────────────────────────────────


class TestCheckCleanBranch:
    """Tests for _check_clean_branch — git state validation."""

    def test_rejects_dirty_worktree(self) -> None:

        dirty_result = MagicMock(stdout=" M file.py\n", returncode=0)
        with (
            patch("metaproc.commands.gcp.subprocess.run", return_value=dirty_result),
            pytest.raises(typer.Exit),
        ):
            _check_clean_branch()

    def test_rejects_unpushed_branch(self) -> None:

        clean = MagicMock(stdout="", returncode=0)
        branch = MagicMock(stdout="my-branch\n", returncode=0)
        sha = MagicMock(stdout="abc1234\n", returncode=0)
        remote_sha = MagicMock(stdout="different\n", returncode=0)

        results = [clean, branch, sha, remote_sha]

        with (
            patch("metaproc.commands.gcp.subprocess.run", side_effect=results),
            pytest.raises(typer.Exit),
        ):
            _check_clean_branch()

    def test_returns_branch_and_sha(self) -> None:

        clean = MagicMock(stdout="", returncode=0)
        branch = MagicMock(stdout="main\n", returncode=0)
        sha = MagicMock(stdout="abc1234def\n", returncode=0)
        remote_sha = MagicMock(stdout="abc1234def\n", returncode=0)

        results = [clean, branch, sha, remote_sha]

        with patch("metaproc.commands.gcp.subprocess.run", side_effect=results):
            b, s = _check_clean_branch()
            assert b == "main"
            assert s == "abc1234def"


class TestBuildWorktreeScript:
    """Tests for _build_worktree_script — remote shell script construction."""

    def test_includes_worktree_add(self) -> None:

        script = _build_worktree_script(
            "my-session", "main", "abc1234", "metaproc run-process mine"
        )
        assert "git worktree add" in script
        assert "abc1234" in script
        assert "my-session" in script
        assert "tmux new-session" in script
        assert "metaproc run-process mine" in script

    def test_uses_session_name_for_worktree_dir(self) -> None:

        script = _build_worktree_script(
            "debug-500", "feat", "deadbeef", "metaproc run-process mine"
        )
        assert "metaproc-worktrees/debug-500" in script


class TestRemoteEditableInstallCommand:
    def test_standalone_defaults_to_repo_root(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("metaproc.commands.gcp.get_bootstrap_env_vars", return_value={}),
        ):
            assert _remote_editable_install_command() == "uv pip install -e ."

    def test_plugin_can_supply_monorepo_package_paths(self) -> None:
        defaults = {
            "METAPROC_REPO_PACKAGE_PATH": "framework",
            "METAPROC_WORKSPACE_PACKAGES": "workflow,plugins/extra",
        }
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("metaproc.commands.gcp.get_bootstrap_env_vars", return_value=defaults),
        ):
            assert (
                _remote_editable_install_command()
                == "uv pip install -e framework -e workflow -e plugins/extra"
            )


class TestBuildEnvExports:
    """Tests for _build_env_exports — env var propagation to remote sessions."""

    def test_exports_set_vars(self) -> None:

        with patch.dict(
            "os.environ",
            {
                "METAPROC_GCP_PROJECT": "my-project",
                "RUNS_DIR": "/mnt/filestore/runs",
            },
        ):
            exports = _build_env_exports()
            assert "METAPROC_GCP_PROJECT" in exports
            assert "my-project" in exports
            assert "RUNS_DIR" in exports

    def test_skips_unset_vars(self) -> None:

        with (
            patch.dict("os.environ", {}, clear=True),
            patch("metaproc.commands.gcp.get_bootstrap_env_vars", return_value={}),
        ):
            exports = _build_env_exports()
            assert exports == "true"

    def test_env_exports_in_worktree_script(self) -> None:

        with patch.dict("os.environ", {"METAPROC_GCP_PROJECT": "proj-1"}):
            script = _build_worktree_script(
                "test-session", "main", "abc123", "metaproc run-process mine"
            )
            assert "METAPROC_GCP_PROJECT" in script
            assert "proj-1" in script


class TestGcpRemoteRun:
    """Tests for the gcp remote-run command."""

    def test_help_text(self) -> None:

        runner = CliRunner()
        result = runner.invoke(app, ["gcp", "remote-run", "--help"])
        assert result.exit_code == 0
        # Strip ANSI codes — Rich formatting breaks flags across lines in CI.
        plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert "tmux" in plain
        assert "--list" in plain
        assert "--attach" in plain
        assert "--kill" in plain
        assert "--cleanup" in plain

    def test_list_mode(self) -> None:

        runner = CliRunner()
        ssh_result = MagicMock(returncode=0)
        with (
            patch("metaproc.commands.gcp.subprocess.run", return_value=ssh_result) as mock_run,
            patch.dict(
                "os.environ",
                {
                    "METAPROC_GATEWAY_HOST": "test-host",
                    "METAPROC_GCP_PROJECT": "test-project",
                },
            ),
        ):
            result = runner.invoke(app, ["gcp", "remote-run", "--list"])

        assert result.exit_code == 0
        remote_cmd = mock_run.call_args[0][0][-1]
        assert "tmux list-sessions" in remote_cmd

    def test_attach_requires_session_name(self) -> None:

        runner = CliRunner()
        with patch.dict(
            "os.environ",
            {
                "METAPROC_GATEWAY_HOST": "test-host",
                "METAPROC_GCP_PROJECT": "test-project",
            },
        ):
            result = runner.invoke(app, ["gcp", "remote-run", "--attach"])

        assert result.exit_code != 0

    def test_kill_requires_session_name(self) -> None:

        runner = CliRunner()
        with patch.dict(
            "os.environ",
            {
                "METAPROC_GATEWAY_HOST": "test-host",
                "METAPROC_GCP_PROJECT": "test-project",
            },
        ):
            result = runner.invoke(app, ["gcp", "remote-run", "--kill"])

        assert result.exit_code != 0

    def test_cleanup_mode(self) -> None:

        runner = CliRunner()
        ssh_result = MagicMock(returncode=0)
        with (
            patch("metaproc.commands.gcp.subprocess.run", return_value=ssh_result) as mock_run,
            patch.dict(
                "os.environ",
                {
                    "METAPROC_GATEWAY_HOST": "test-host",
                    "METAPROC_GCP_PROJECT": "test-project",
                },
            ),
        ):
            result = runner.invoke(app, ["gcp", "remote-run", "--cleanup"])

        assert result.exit_code == 0
        remote_cmd = mock_run.call_args[0][0][-1]
        assert "worktree" in remote_cmd
        assert "prune" in remote_cmd

    def test_requires_project(self) -> None:

        runner = CliRunner()
        with patch.dict("os.environ", {}, clear=True):
            result = runner.invoke(app, ["gcp", "remote-run", "--list", "--host", "h"])

        assert result.exit_code != 0

    def test_launch_propagates_no_spot_and_continue_on_error(self) -> None:

        runner = CliRunner()
        preflight = MagicMock(returncode=0, stdout="OK: tmux, uv, git available", stderr="")
        launch = MagicMock(returncode=0, stdout="", stderr="")
        with (
            patch(
                "metaproc.commands.gcp.subprocess.run",
                side_effect=[preflight, launch],
            ) as mock_run,
            patch(
                "metaproc.commands.gcp._check_clean_branch",
                return_value=("test-branch", "deadbeef" * 5),
            ),
            patch("metaproc.commands.gcp._ensure_remote_repo"),
            patch.dict(
                "os.environ",
                {
                    "METAPROC_GATEWAY_HOST": "test-host",
                    "METAPROC_GCP_PROJECT": "test-project",
                },
            ),
        ):
            result = runner.invoke(
                app,
                [
                    "gcp",
                    "remote-run",
                    "example_plugin/process/mine",
                    "--var",
                    "RUN_ID=p4a-test",
                    "--variant",
                    "pi-glm-5",
                    "--num-workers",
                    "2",
                    "--max-concurrency",
                    "15",
                    "--no-spot",
                    "--continue-on-error",
                ],
            )

        assert result.exit_code == 0, result.output
        launch_script = mock_run.call_args_list[1][0][0][-1]
        assert "--no-spot" in launch_script
        assert "--continue-on-error" in launch_script
        assert "--num-workers 2" in launch_script
        assert "--max-concurrency 15" in launch_script
        assert "pi-glm-5" in launch_script
