"""Tests for cloud/gcp/worker_dispatch.py — worker VM dispatch extraction."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from metaproc.cloud.gcp.batch_backend import GCPBatchConfig
from metaproc.cloud.gcp.worker_dispatch import (
    WorkerDispatchConfig,
    _read_nfs_pool_status,
    _submit_workers,
    build_gcp_config_from_env,
    partition_items,
)
from metaproc.dispatch.auth_pool_flags import AuthPoolFlags
from metaproc.osutils.memory_pressure import PressureLevel
from metaproc.paths import POOL_STATUS_FILE, STATE_DIR, step_state_dir, worker_state_dir
from metaproc.runpool.status import PressureStatus, RunPoolStatus, write_status

# ── partition_items ─────────────────────────────────────────────


class TestPartitionItems:
    def test_round_robin_3_workers(self):
        contexts = [
            {"event_id": "A", "ticker": "AAPL"},
            {"event_id": "B", "ticker": "GOOG"},
            {"event_id": "C", "ticker": "MSFT"},
            {"event_id": "D", "ticker": "AMZN"},
            {"event_id": "E", "ticker": "META"},
            {"event_id": "F", "ticker": "NVDA"},
            {"event_id": "G", "ticker": "TSLA"},
        ]
        items, ctx_parts = partition_items(contexts, "event_id", 3)
        assert items[0] == ["A", "D", "G"]
        assert items[1] == ["B", "E"]
        assert items[2] == ["C", "F"]
        assert len(ctx_parts[0]) == 3
        assert ctx_parts[0][0]["ticker"] == "AAPL"

    def test_single_worker(self):
        contexts = [{"event_id": "A"}, {"event_id": "B"}, {"event_id": "C"}]
        items, ctx_parts = partition_items(contexts, "event_id", 1)
        assert items[0] == ["A", "B", "C"]
        assert len(ctx_parts) == 1

    def test_more_workers_than_items(self):
        contexts = [{"event_id": "A"}, {"event_id": "B"}]
        items, _ctx_parts = partition_items(contexts, "event_id", 5)
        assert len(items) == 2  # capped at item count
        assert items[0] == ["A"]
        assert items[1] == ["B"]


# ── build_gcp_config_from_env ───────────────────────────────────


class TestBuildGCPConfigFromEnv:
    def test_valid_env(self):
        env = {
            "METAPROC_GCP_PROJECT": "test-project",
            "METAPROC_GCP_CONTAINER_IMAGE": "gcr.io/test/img:latest",
            "METAPROC_GCP_FILESTORE_SERVER": "10.0.0.1",
            "RUNS_DIR": "runs",
        }
        with patch.dict(os.environ, env, clear=True):
            config = build_gcp_config_from_env()
        assert config.project == "test-project"
        assert config.container_image == "gcr.io/test/img:latest"
        assert config.filestore_server == "10.0.0.1"
        assert config.machine_type == "n2-highmem-8"  # default

    def test_custom_machine_type(self):
        env = {
            "METAPROC_GCP_PROJECT": "test-project",
            "METAPROC_GCP_CONTAINER_IMAGE": "gcr.io/test/img:latest",
            "METAPROC_GCP_FILESTORE_SERVER": "10.0.0.1",
        }
        with patch.dict(os.environ, env, clear=True):
            config = build_gcp_config_from_env(machine_type="n2-highmem-16")
        assert config.machine_type == "n2-highmem-16"

    def test_missing_project_raises(self):
        env = {
            "METAPROC_GCP_CONTAINER_IMAGE": "gcr.io/test/img:latest",
            "METAPROC_GCP_FILESTORE_SERVER": "10.0.0.1",
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="METAPROC_GCP_PROJECT"):
                build_gcp_config_from_env()

    def test_missing_container_image_raises(self):
        env = {
            "METAPROC_GCP_PROJECT": "test-project",
            "METAPROC_GCP_FILESTORE_SERVER": "10.0.0.1",
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="METAPROC_GCP_CONTAINER_IMAGE"):
                build_gcp_config_from_env()

    def test_missing_filestore_raises(self):
        env = {
            "METAPROC_GCP_PROJECT": "test-project",
            "METAPROC_GCP_CONTAINER_IMAGE": "gcr.io/test/img:latest",
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="METAPROC_GCP_FILESTORE_SERVER"):
                build_gcp_config_from_env()

    def test_spot_flag(self):
        env = {
            "METAPROC_GCP_PROJECT": "test-project",
            "METAPROC_GCP_CONTAINER_IMAGE": "gcr.io/test/img:latest",
            "METAPROC_GCP_FILESTORE_SERVER": "10.0.0.1",
        }
        with patch.dict(os.environ, env, clear=True):
            config = build_gcp_config_from_env(spot=False)
        assert config.spot is False


# ── WorkerDispatchConfig ────────────────────────────────────────


class TestWorkerDispatchConfig:
    def test_defaults(self):

        gcp = GCPBatchConfig(project="test")
        config = WorkerDispatchConfig(gcp=gcp)
        assert config.num_workers == 2
        assert config.max_concurrency == 50
        assert config.max_retries is None
        assert config.poll_interval == 60
        assert config.spot is True


class TestWorkerDispatchRuntimeVars:
    def test_submit_workers_rewrites_runs_dir_for_filestore(self):

        gcp_config = GCPBatchConfig(
            project="test-project",
            region="us-central1",
            container_image="gcr.io/test/agent:latest",
            filestore_server="10.0.0.1",
            filestore_mount_path="/mnt/filestore",
        )
        config = WorkerDispatchConfig(
            gcp=gcp_config,
            num_workers=1,
            variant="deepseek",
        )

        mock_client = MagicMock()
        mock_job = MagicMock()
        mock_job.name = "projects/p/locations/r/jobs/j"
        mock_client.create_job.return_value = mock_job

        with patch(
            "google.cloud.batch_v1.BatchServiceClient",
            return_value=mock_client,
        ):
            asyncio.run(
                _submit_workers(
                    partitions=[["AAPL"]],
                    context_partitions=[[{"TICKER": "AAPL"}]],
                    config=config,
                    step="mine",
                    process_spec_rel="example_plugin",
                    vars_json=json.dumps(
                        {
                            "RUN_ID": "test-run",
                            "RUNS_DIR": "runs/local/example-workflow",
                        }
                    ),
                    run_dir=None,
                    out=MagicMock(),
                )
            )

        request = (
            mock_client.create_job.call_args.kwargs.get("request")
            or mock_client.create_job.call_args[0][0]
        )
        env_vars = request.job.task_groups[0].task_spec.runnables[1].environment.variables

        assert env_vars["RUNS_DIR"] == "/mnt/filestore/runs"
        assert json.loads(env_vars["METAPROC_VARS"]) == {
            "RUN_ID": "test-run",
            "RUNS_DIR": "/mnt/filestore/runs",
        }

    def test_submit_workers_forwards_auth_pool_env_vars(self, monkeypatch):
        """Phase 6 — closes the gap.

        When the orchestrator process has METAPROC_AUTH_* env vars set
        (which it does after review), worker_dispatch
        must propagate them onto each fan-out worker Batch job's
        environment so the worker entrypoint can rebuild --auth-* flags
        on the inner run-parallel command. Without this, cloud workers
        silently fall back to the legacy single-credential bootstrap.
        """

        # Simulate the orchestrator's environment as set by
        # orchestrator_dispatch (review). All five auth vars present.
        monkeypatch.setenv("METAPROC_AUTH_ACCOUNT", "claude-code-cli")
        monkeypatch.setenv("METAPROC_AUTH_BACKEND", "gcp-secret-manager")
        monkeypatch.setenv("METAPROC_AUTH_FALLBACK_POLICY", "same-provider")
        monkeypatch.setenv("METAPROC_AUTH_INCLUDE_LABELS", "alt1,alt2")
        monkeypatch.setenv("METAPROC_AUTH_EXCLUDE_LABELS", "")

        gcp_config = GCPBatchConfig(
            project="test-project",
            region="us-central1",
            container_image="gcr.io/test/agent:latest",
            filestore_server="10.0.0.1",
            filestore_mount_path="/mnt/filestore",
        )
        config = WorkerDispatchConfig(
            gcp=gcp_config,
            num_workers=1,
            variant="claude-code-cli",
        )

        mock_client = MagicMock()
        mock_job = MagicMock()
        mock_job.name = "projects/p/locations/r/jobs/j"
        mock_client.create_job.return_value = mock_job

        with patch(
            "google.cloud.batch_v1.BatchServiceClient",
            return_value=mock_client,
        ):
            asyncio.run(
                _submit_workers(
                    partitions=[["AAPL"]],
                    context_partitions=[[{"TICKER": "AAPL"}]],
                    config=config,
                    step="predict-ticker",
                    process_spec_rel="example_plugin/process/predict/predict.process.md",
                    vars_json=json.dumps({"RUN_ID": "test-run"}),
                    run_dir=None,
                    out=MagicMock(),
                )
            )

        request = (
            mock_client.create_job.call_args.kwargs.get("request")
            or mock_client.create_job.call_args[0][0]
        )
        env_vars = request.job.task_groups[0].task_spec.runnables[1].environment.variables

        # Each set env var must be forwarded verbatim. Empty values are
        # dropped (matches the orchestrator-leg's behavior — the worker
        # entrypoint treats "" as "omit this flag").
        assert env_vars["METAPROC_AUTH_ACCOUNT"] == "claude-code-cli"
        assert env_vars["METAPROC_AUTH_BACKEND"] == "gcp-secret-manager"
        assert env_vars["METAPROC_AUTH_FALLBACK_POLICY"] == "same-provider"
        assert env_vars["METAPROC_AUTH_INCLUDE_LABELS"] == "alt1,alt2"
        # Empty vars are skipped — they encode "no exclusions".
        assert "METAPROC_AUTH_EXCLUDE_LABELS" not in env_vars

    def test_submit_workers_uses_config_auth_flags_when_set(self, monkeypatch):
        """The Batch worker dispatch must prefer its explicit auth flags.

        The full-cloud orchestrator passes resolved AuthPoolFlags on
        WorkerDispatchConfig.auth_flags. A from_env() fallback at the worker
        dispatch site could silently drop that auth chain, so config.auth_flags
        must win over an empty ambient environment.
        """

        # The orchestrator ambient env has no auth-pool variables set.
        for var in (
            "METAPROC_AUTH_ACCOUNT",
            "METAPROC_AUTH_BACKEND",
            "METAPROC_AUTH_FALLBACK_POLICY",
            "METAPROC_AUTH_INCLUDE_LABELS",
            "METAPROC_AUTH_EXCLUDE_LABELS",
        ):
            monkeypatch.delenv(var, raising=False)

        gcp_config = GCPBatchConfig(
            project="test-project",
            region="us-central1",
            container_image="gcr.io/test/agent:latest",
            filestore_server="10.0.0.1",
        )
        # Resolved auth flags as the outer cloud request would build them.
        config = WorkerDispatchConfig(
            gcp=gcp_config,
            num_workers=1,
            variant="claude-code-cli",
            auth_flags=AuthPoolFlags(
                auth_account="claude-code-cli",
                auth_backend="gcp-secret-manager",
                auth_fallback_policy="same-provider",
                auth_include_labels=("alt1", "alt2"),
            ),
        )

        mock_client = MagicMock()
        mock_job = MagicMock()
        mock_job.name = "projects/p/locations/r/jobs/j"
        mock_client.create_job.return_value = mock_job

        with patch("google.cloud.batch_v1.BatchServiceClient", return_value=mock_client):
            asyncio.run(
                _submit_workers(
                    partitions=[["AAPL"]],
                    context_partitions=[[{"TICKER": "AAPL"}]],
                    config=config,
                    step="predict-ticker",
                    process_spec_rel="example_plugin/process/predict/predict.process.md",
                    vars_json=json.dumps({"RUN_ID": "test-run"}),
                    run_dir=None,
                    out=MagicMock(),
                )
            )

        request = (
            mock_client.create_job.call_args.kwargs.get("request")
            or mock_client.create_job.call_args[0][0]
        )
        env_vars = request.job.task_groups[0].task_spec.runnables[1].environment.variables

        # The resolved flags from config — including the worker-default
        # backend the operator never typed — must reach the worker env.
        assert env_vars["METAPROC_AUTH_ACCOUNT"] == "claude-code-cli"
        assert env_vars["METAPROC_AUTH_BACKEND"] == "gcp-secret-manager"
        assert env_vars["METAPROC_AUTH_FALLBACK_POLICY"] == "same-provider"
        assert env_vars["METAPROC_AUTH_INCLUDE_LABELS"] == "alt1,alt2"

    def test_submit_workers_config_auth_flags_override_ambient_env(self, monkeypatch):
        """Regression coverage: config wins over ambient env.

        If the orchestrator both has METAPROC_AUTH_* set AND passes a
        config.auth_flags (mixed legacy + new caller), the explicit
        config wins. Prevents stale orchestrator env from leaking onto
        a freshly-resolved dispatch config.
        """

        # Stale ambient env values.
        monkeypatch.setenv("METAPROC_AUTH_ACCOUNT", "stale-adapter")
        monkeypatch.setenv("METAPROC_AUTH_BACKEND", "local")
        monkeypatch.setenv("METAPROC_AUTH_INCLUDE_LABELS", "stale-label")

        gcp_config = GCPBatchConfig(
            project="test-project",
            region="us-central1",
            container_image="gcr.io/test/agent:latest",
            filestore_server="10.0.0.1",
        )
        config = WorkerDispatchConfig(
            gcp=gcp_config,
            num_workers=1,
            variant="claude-code-cli",
            auth_flags=AuthPoolFlags(
                auth_account="claude-code-cli",
                auth_backend="gcp-secret-manager",
                auth_include_labels=("alt1",),
            ),
        )

        mock_client = MagicMock()
        mock_job = MagicMock()
        mock_job.name = "projects/p/locations/r/jobs/j"
        mock_client.create_job.return_value = mock_job

        with patch("google.cloud.batch_v1.BatchServiceClient", return_value=mock_client):
            asyncio.run(
                _submit_workers(
                    partitions=[["AAPL"]],
                    context_partitions=[[{"TICKER": "AAPL"}]],
                    config=config,
                    step="predict-ticker",
                    process_spec_rel="example_plugin/process/predict/predict.process.md",
                    vars_json=json.dumps({"RUN_ID": "test-run"}),
                    run_dir=None,
                    out=MagicMock(),
                )
            )

        request = (
            mock_client.create_job.call_args.kwargs.get("request")
            or mock_client.create_job.call_args[0][0]
        )
        env_vars = request.job.task_groups[0].task_spec.runnables[1].environment.variables

        # Config values, not ambient ones, reached the worker.
        assert env_vars["METAPROC_AUTH_ACCOUNT"] == "claude-code-cli"
        assert env_vars["METAPROC_AUTH_BACKEND"] == "gcp-secret-manager"
        assert env_vars["METAPROC_AUTH_INCLUDE_LABELS"] == "alt1"

    def test_submit_workers_skips_auth_vars_when_orchestrator_unset(self, monkeypatch):
        """Without --auth-* on the outer dispatch, no METAPROC_AUTH_* env vars
        are set on the orchestrator. Worker jobs must not inherit anything,
        and the legacy single-credential bootstrap path remains intact.
        """

        for var in (
            "METAPROC_AUTH_ACCOUNT",
            "METAPROC_AUTH_BACKEND",
            "METAPROC_AUTH_FALLBACK_POLICY",
            "METAPROC_AUTH_INCLUDE_LABELS",
            "METAPROC_AUTH_EXCLUDE_LABELS",
        ):
            monkeypatch.delenv(var, raising=False)

        gcp_config = GCPBatchConfig(
            project="test-project",
            region="us-central1",
            container_image="gcr.io/test/agent:latest",
            filestore_server="10.0.0.1",
        )
        config = WorkerDispatchConfig(gcp=gcp_config, num_workers=1, variant="deepseek")

        mock_client = MagicMock()
        mock_job = MagicMock()
        mock_job.name = "projects/p/locations/r/jobs/j"
        mock_client.create_job.return_value = mock_job

        with patch("google.cloud.batch_v1.BatchServiceClient", return_value=mock_client):
            asyncio.run(
                _submit_workers(
                    partitions=[["AAPL"]],
                    context_partitions=[[{"TICKER": "AAPL"}]],
                    config=config,
                    step="mine",
                    process_spec_rel="example_plugin",
                    vars_json=json.dumps({"RUN_ID": "test-run"}),
                    run_dir=None,
                    out=MagicMock(),
                )
            )

        request = (
            mock_client.create_job.call_args.kwargs.get("request")
            or mock_client.create_job.call_args[0][0]
        )
        env_vars = request.job.task_groups[0].task_spec.runnables[1].environment.variables

        for var in (
            "METAPROC_AUTH_ACCOUNT",
            "METAPROC_AUTH_BACKEND",
            "METAPROC_AUTH_FALLBACK_POLICY",
            "METAPROC_AUTH_INCLUDE_LABELS",
            "METAPROC_AUTH_EXCLUDE_LABELS",
        ):
            assert var not in env_vars, (
                f"{var} leaked into worker env when orchestrator had no auth"
            )

    def test_submit_workers_preserves_valid_suffix_in_long_job_id(self):

        gcp_config = GCPBatchConfig(
            project="test-project",
            region="us-central1",
            container_image="gcr.io/test/agent:latest",
            filestore_server="10.0.0.1",
        )
        config = WorkerDispatchConfig(gcp=gcp_config, num_workers=1, variant="deepseek")

        mock_client = MagicMock()
        mock_job = MagicMock()
        mock_job.name = "projects/p/locations/r/jobs/j"
        mock_client.create_job.return_value = mock_job

        with (
            patch("google.cloud.batch_v1.BatchServiceClient", return_value=mock_client),
            patch("metaproc.cloud.gcp.worker_dispatch.time.time", return_value=1776328315),
            patch("metaproc.cloud.gcp.worker_dispatch._secrets.token_hex", return_value="abc123"),
        ):
            asyncio.run(
                _submit_workers(
                    partitions=[["AAPL"]],
                    context_partitions=[[{"TICKER": "AAPL"}]],
                    config=config,
                    step="mine",
                    process_spec_rel="example_plugin",
                    vars_json=json.dumps(
                        {
                            "RUN_ID": "mine-smoke-us-traded-20-2026-04-16-cloud-replaysa",
                        }
                    ),
                    run_dir=None,
                    out=MagicMock(),
                )
            )

        request = (
            mock_client.create_job.call_args.kwargs.get("request")
            or mock_client.create_job.call_args[0][0]
        )
        assert request.job_id.endswith("-w0-1776328315-abc123")
        assert len(request.job_id) <= 63
        assert not request.job_id.endswith("-")

    def test_submit_workers_spills_large_context_payload_to_file(self, tmp_path: Path):

        gcp_config = GCPBatchConfig(
            project="test-project",
            region="us-central1",
            container_image="gcr.io/test/agent:latest",
            filestore_server="10.0.0.1",
        )
        config = WorkerDispatchConfig(
            gcp=gcp_config,
            num_workers=1,
            variant="deepseek",
        )

        large_contexts = [{"TICKER": f"SYM{i:04d}", "SECTOR": "X" * 200} for i in range(200)]

        mock_client = MagicMock()
        mock_job = MagicMock()
        mock_job.name = "projects/p/locations/r/jobs/j"
        mock_client.create_job.return_value = mock_job

        with patch(
            "google.cloud.batch_v1.BatchServiceClient",
            return_value=mock_client,
        ):
            asyncio.run(
                _submit_workers(
                    partitions=[["AAPL"]],
                    context_partitions=[large_contexts],
                    config=config,
                    step="mine",
                    process_spec_rel="example_plugin",
                    vars_json=json.dumps({"RUN_ID": "test-run"}),
                    run_dir=tmp_path,
                    out=MagicMock(),
                )
            )

        request = (
            mock_client.create_job.call_args.kwargs.get("request")
            or mock_client.create_job.call_args[0][0]
        )
        env_vars = request.job.task_groups[0].task_spec.runnables[1].environment.variables

        assert "METAPROC_ITEM_CONTEXTS" not in env_vars
        payload_path = Path(env_vars["METAPROC_ITEM_CONTEXTS_FILE"])

        assert (
            payload_path
            == step_state_dir(tmp_path, "mine") / "worker_payloads" / "worker-0-item-contexts.json"
        )
        assert json.loads(payload_path.read_text()) == large_contexts


# ── Worker-scoped pool state aggregation (RF-3) ─────────────────


class TestWorkerScopedPoolStatus:
    def _make_pool_status(self, completed: int = 0, failed: int = 0, active: int = 0):

        return RunPoolStatus(
            pool_id="test-pool",
            pid=12345,
            started_at="2026-04-09T00:00:00",
            updated_at="2026-04-09T00:01:00",
            backend="local",
            max_concurrency=10,
            current_concurrency=5,
            active_count=active,
            pending_count=0,
            completed_count=completed,
            failed_count=failed,
            killed_count=0,
            pressure=PressureStatus(
                level=PressureLevel.NORMAL,
                available_pct=80.0,
                swap_used_gb=0.0,
                total_memory_gb=32.0,
                source="test",
            ),
        )

    def test_aggregate_across_worker_dirs(self, tmp_path):
        """Multiple worker state dirs should be aggregated."""

        for wi in range(3):
            worker_dir = worker_state_dir(tmp_path, wi)
            worker_dir.mkdir(parents=True)
            write_status(
                worker_dir / POOL_STATUS_FILE,
                self._make_pool_status(completed=10, failed=1, active=2),
            )

        completed, failed, active = _read_nfs_pool_status(str(tmp_path))
        assert completed == 30
        assert failed == 3
        assert active == 6

    def test_empty_dir(self, tmp_path):

        assert _read_nfs_pool_status(str(tmp_path)) == (0, 0, 0)

    def test_legacy_shared_status_still_works(self, tmp_path):
        """Legacy single shared status file should still be read."""

        state_dir = tmp_path / STATE_DIR
        state_dir.mkdir(parents=True)
        write_status(
            state_dir / POOL_STATUS_FILE,
            self._make_pool_status(completed=7, failed=2, active=1),
        )

        completed, failed, active = _read_nfs_pool_status(str(tmp_path))
        assert completed == 7
        assert failed == 2
        assert active == 1
