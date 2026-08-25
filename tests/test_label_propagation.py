"""Tests for allocationPolicy.labels propagation across all dispatch paths.

Both Batch dispatch paths (worker_dispatch, orchestrator_dispatch) must set labels
on both Job.labels and allocation_policy.labels so labels propagate to VM/disk
resources for billing attribution and Cloud Monitoring per-resource metrics.
"""

# pyright: reportAttributeAccessIssue=false

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest
from google.cloud.batch_v1.types import JobStatus

from metaproc.cloud.gcp.batch_backend import (
    GCPBatchConfig,
    run_identity_label,
)
from metaproc.cloud.gcp.orchestrator_dispatch import (
    OrchestratorDispatchConfig,
    dispatch_orchestrator,
)
from metaproc.cloud.gcp.secret_hydration import SECRET_REFS_ENV
from metaproc.cloud.gcp.worker_dispatch import WorkerDispatchConfig, _submit_workers
from metaproc.dispatch.secret_refs import SecretRefSet

# ── Secret reference registry ────────────────────────────────────


class TestBuildSecretEnvVars:
    def test_empty_when_no_secret_vars_set(self, monkeypatch):
        monkeypatch.delenv("METAPROC_GCP_SECRET_GH_TOKEN", raising=False)
        monkeypatch.delenv("METAPROC_GCP_SECRET_CLAUDE_CREDS", raising=False)
        assert SecretRefSet.all_known().resolve_env_refs() == {}

    def test_gh_token_mapping(self, monkeypatch):
        monkeypatch.setenv(
            "METAPROC_GCP_SECRET_GH_TOKEN",
            "projects/p/secrets/gh-token/versions/latest",
        )
        monkeypatch.delenv("METAPROC_GCP_SECRET_CLAUDE_CREDS", raising=False)
        assert SecretRefSet.all_known().resolve_env_refs() == {
            "GH_TOKEN": "projects/p/secrets/gh-token/versions/latest",
        }

    def test_claude_creds_mapping(self, monkeypatch):
        monkeypatch.delenv("METAPROC_GCP_SECRET_GH_TOKEN", raising=False)
        monkeypatch.setenv(
            "METAPROC_GCP_SECRET_CLAUDE_CREDS",
            "projects/p/secrets/claude-code-creds-ryan/versions/latest",
        )
        assert SecretRefSet.all_known().resolve_env_refs() == {
            "CLAUDE_CODE_CREDS_JSON": "projects/p/secrets/claude-code-creds-ryan/versions/latest",
        }

    def test_both_secrets_mapped_together(self, monkeypatch):
        monkeypatch.setenv(
            "METAPROC_GCP_SECRET_GH_TOKEN",
            "projects/p/secrets/gh-token/versions/latest",
        )
        monkeypatch.setenv(
            "METAPROC_GCP_SECRET_CLAUDE_CREDS",
            "projects/p/secrets/claude-code-creds-ryan/versions/3",
        )
        assert SecretRefSet.all_known().resolve_env_refs() == {
            "GH_TOKEN": "projects/p/secrets/gh-token/versions/latest",
            "CLAUDE_CODE_CREDS_JSON": "projects/p/secrets/claude-code-creds-ryan/versions/3",
        }


# ── worker_dispatch.py ───────────────────────────────────────────


class TestWorkerDispatchLabelPropagation:
    def test_allocation_policy_labels_match_job_labels(self):

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

        mock_client = MagicMock()
        mock_job = MagicMock()
        mock_job.name = "projects/p/locations/r/jobs/j"
        mock_client.create_job.return_value = mock_job

        vars_json = json.dumps({"RUN_ID": "test-run"})

        with patch(
            "google.cloud.batch_v1.BatchServiceClient",
            return_value=mock_client,
        ):
            asyncio.run(
                _submit_workers(
                    partitions=[["AAPL", "GOOG"]],
                    context_partitions=[[{"TICKER": "AAPL"}, {"TICKER": "GOOG"}]],
                    config=config,
                    step="mine",
                    process_spec_rel="example_plugin",
                    vars_json=vars_json,
                    run_dir=None,
                    out=MagicMock(),
                )
            )

        request = (
            mock_client.create_job.call_args.kwargs.get("request")
            or mock_client.create_job.call_args[0][0]
        )
        job = request.job
        task_spec = job.task_groups[0].task_spec

        assert job.labels, "Job.labels should not be empty"
        assert job.allocation_policy.labels, "allocation_policy.labels should not be empty"
        assert job.allocation_policy.labels == job.labels
        assert job.labels["metaproc-role"] == "worker"
        assert job.labels["metaproc-run-id"] == "test-run"
        assert job.labels["metaproc-run-key"] == run_identity_label("test-run")
        assert len(task_spec.runnables) == 2
        assert task_spec.runnables[0].display_name == "mount-filestore"
        assert "mount -t nfs" in task_spec.runnables[0].script.text
        assert task_spec.runnables[1].container.volumes == ["/mnt/filestore:/mnt/filestore:rw"]
        assert not task_spec.volumes


# ── orchestrator_dispatch.py ─────────────────────────────────────


class TestOrchestratorDispatchLabelPropagation:
    def test_allocation_policy_labels_match_job_labels(self):

        gcp_config = GCPBatchConfig(
            project="test-project",
            region="us-central1",
            container_image="gcr.io/test/agent:latest",
            filestore_server="10.0.0.1",
        )
        config = OrchestratorDispatchConfig(
            gcp=gcp_config,
            process_spec_rel="example_plugin",
            variables={"RUN_ID": "test-run"},
            variant="deepseek",
            poll_interval=0,
        )

        mock_client = MagicMock()
        mock_job = MagicMock()
        mock_job.name = "projects/p/locations/r/jobs/j"
        mock_job.status.state = JobStatus.State.SUCCEEDED
        mock_client.create_job.return_value = mock_job
        mock_client.get_job.return_value = mock_job

        with patch(
            "google.cloud.batch_v1.BatchServiceClient",
            return_value=mock_client,
        ):
            asyncio.run(dispatch_orchestrator(config))

        request = (
            mock_client.create_job.call_args.kwargs.get("request")
            or mock_client.create_job.call_args[0][0]
        )
        job = request.job
        task_spec = job.task_groups[0].task_spec

        assert job.labels, "Job.labels should not be empty"
        assert job.allocation_policy.labels, "allocation_policy.labels should not be empty"
        assert job.allocation_policy.labels == job.labels
        assert job.labels["metaproc-role"] == "orchestrator"
        assert job.labels["metaproc-run-id"] == "test-run"
        assert job.labels["metaproc-run-key"] == run_identity_label("test-run")
        assert len(task_spec.runnables) == 2
        assert task_spec.runnables[0].display_name == "mount-filestore"
        assert "mount -t nfs" in task_spec.runnables[0].script.text
        assert task_spec.runnables[1].container.volumes == ["/mnt/filestore:/mnt/filestore:rw"]
        assert not task_spec.volumes

    def test_metaproc_vars_rewrites_runs_dir_for_filestore(self):

        gcp_config = GCPBatchConfig(
            project="test-project",
            region="us-central1",
            container_image="gcr.io/test/agent:latest",
            filestore_server="10.0.0.1",
            filestore_mount_path="/mnt/filestore",
        )
        config = OrchestratorDispatchConfig(
            gcp=gcp_config,
            process_spec_rel="example_plugin",
            variables={
                "RUN_ID": "test-run",
                "RUNS_DIR": "runs/local/example-workflow",
            },
            variant="deepseek",
            poll_interval=0,
        )

        mock_client = MagicMock()
        mock_job = MagicMock()
        mock_job.name = "projects/p/locations/r/jobs/j"
        mock_job.status.state = JobStatus.State.SUCCEEDED
        mock_client.create_job.return_value = mock_job
        mock_client.get_job.return_value = mock_job

        with patch(
            "google.cloud.batch_v1.BatchServiceClient",
            return_value=mock_client,
        ):
            asyncio.run(dispatch_orchestrator(config))

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

    def test_num_workers_env_is_propagated_to_orchestrator_entrypoint(self):

        gcp_config = GCPBatchConfig(
            project="test-project",
            region="us-central1",
            container_image="gcr.io/test/agent:latest",
            filestore_server="10.0.0.1",
        )
        config = OrchestratorDispatchConfig(
            gcp=gcp_config,
            process_spec_rel="example_plugin",
            variables={"RUN_ID": "test-run"},
            variant="deepseek",
            poll_interval=0,
            num_workers=2,
        )

        mock_client = MagicMock()
        mock_job = MagicMock()
        mock_job.name = "projects/p/locations/r/jobs/j"
        mock_job.status.state = JobStatus.State.SUCCEEDED
        mock_client.create_job.return_value = mock_job
        mock_client.get_job.return_value = mock_job

        with patch(
            "google.cloud.batch_v1.BatchServiceClient",
            return_value=mock_client,
        ):
            asyncio.run(dispatch_orchestrator(config))

        request = (
            mock_client.create_job.call_args.kwargs.get("request")
            or mock_client.create_job.call_args[0][0]
        )
        env_vars = request.job.task_groups[0].task_spec.runnables[1].environment.variables

        assert env_vars["METAPROC_NUM_WORKERS"] == "2"
        assert env_vars["METAPROC_DEFAULT_NUM_WORKERS"] == "2"

    def test_artifact_env_is_forwarded_to_orchestrator(self):

        gcp_config = GCPBatchConfig(
            project="test-project",
            region="us-central1",
            container_image="gcr.io/test/agent:latest",
            filestore_server="10.0.0.1",
        )
        config = OrchestratorDispatchConfig(
            gcp=gcp_config,
            process_spec_rel="example_plugin",
            variables={"RUN_ID": "test-run"},
            poll_interval=0,
        )

        mock_client = MagicMock()
        mock_job = MagicMock()
        mock_job.name = "projects/p/locations/r/jobs/j"
        mock_job.status.state = JobStatus.State.SUCCEEDED
        mock_client.create_job.return_value = mock_job
        mock_client.get_job.return_value = mock_job

        with (
            patch("google.cloud.batch_v1.BatchServiceClient", return_value=mock_client),
            patch.dict(
                "os.environ",
                {
                    "METAPROC_WHEEL_GCS": "gs://dispatch-bucket/wheels/metaproc.whl",
                    "METAPROC_WORKSPACE_GCS": "gs://dispatch-bucket/job/workspace.tar.gz",
                },
                clear=False,
            ),
        ):
            asyncio.run(dispatch_orchestrator(config))

        request = (
            mock_client.create_job.call_args.kwargs.get("request")
            or mock_client.create_job.call_args[0][0]
        )
        env_vars = request.job.task_groups[0].task_spec.runnables[1].environment.variables
        assert env_vars["METAPROC_WHEEL_GCS"] == "gs://dispatch-bucket/wheels/metaproc.whl"
        assert env_vars["METAPROC_WORKSPACE_GCS"] == "gs://dispatch-bucket/job/workspace.tar.gz"

    def test_long_run_id_keeps_valid_orchestrator_job_suffix(self):

        gcp_config = GCPBatchConfig(
            project="test-project",
            region="us-central1",
            container_image="gcr.io/test/agent:latest",
            filestore_server="10.0.0.1",
        )
        config = OrchestratorDispatchConfig(
            gcp=gcp_config,
            process_spec_rel="example_plugin",
            variables={"RUN_ID": "mine-smoke-us-traded-20-2026-04-16-cloud-replaysa"},
            variant="deepseek",
            poll_interval=0,
        )

        mock_client = MagicMock()
        mock_job = MagicMock()
        mock_job.name = "projects/p/locations/r/jobs/j"
        mock_job.status.state = JobStatus.State.SUCCEEDED
        mock_client.create_job.return_value = mock_job
        mock_client.get_job.return_value = mock_job

        with (
            patch("google.cloud.batch_v1.BatchServiceClient", return_value=mock_client),
            patch("metaproc.cloud.gcp.orchestrator_dispatch.time.time", return_value=1776328315),
            patch(
                "metaproc.cloud.gcp.orchestrator_dispatch._secrets.token_hex", return_value="abc123"
            ),
        ):
            asyncio.run(dispatch_orchestrator(config))

        request = (
            mock_client.create_job.call_args.kwargs.get("request")
            or mock_client.create_job.call_args[0][0]
        )
        assert request.job_id.endswith("-1776328315-abc123")
        assert len(request.job_id) <= 63
        assert not request.job_id.endswith("-")

    def test_worker_gcp_env_is_forwarded_to_orchestrator(self):

        gcp_config = GCPBatchConfig(
            project="test-project",
            region="us-central1",
            container_image="gcr.io/test/agent:latest",
            boot_disk_gb=123,
            service_account_email="user@example.invalid",
            network="projects/test-project/global/networks/test-net",
            subnetwork="projects/test-project/regions/us-central1/subnetworks/test-subnet",
            filestore_server="10.0.0.1",
            filestore_share="/test-share",
            filestore_mount_path="/mnt/test-filestore",
        )
        config = OrchestratorDispatchConfig(
            gcp=gcp_config,
            process_spec_rel="example_plugin",
            variables={"RUN_ID": "test-run"},
            variant="pi-glm-5",
            poll_interval=0,
        )

        mock_client = MagicMock()
        mock_job = MagicMock()
        mock_job.name = "projects/p/locations/r/jobs/j"
        mock_job.status.state = JobStatus.State.SUCCEEDED
        mock_client.create_job.return_value = mock_job
        mock_client.get_job.return_value = mock_job

        with (
            patch("google.cloud.batch_v1.BatchServiceClient", return_value=mock_client),
            patch.dict(
                "os.environ",
                {
                    "METAPROC_GCP_SECRET_GH_TOKEN": (
                        "projects/test-project/secrets/gh-token/versions/latest"
                    )
                },
                clear=False,
            ),
        ):
            asyncio.run(dispatch_orchestrator(config))

        request = (
            mock_client.create_job.call_args.kwargs.get("request")
            or mock_client.create_job.call_args[0][0]
        )
        environment = request.job.task_groups[0].task_spec.runnables[1].environment
        env_vars = environment.variables

        assert env_vars["METAPROC_GCP_PROJECT"] == "test-project"
        assert env_vars["METAPROC_GCP_REGION"] == "us-central1"
        assert env_vars["METAPROC_GCP_CONTAINER_IMAGE"] == "gcr.io/test/agent:latest"
        assert env_vars["METAPROC_GCP_ORCHESTRATOR"] == "1"
        assert env_vars["METAPROC_GCP_SERVICE_ACCOUNT"] == ("user@example.invalid")
        assert env_vars["METAPROC_GCP_NETWORK"] == (
            "projects/test-project/global/networks/test-net"
        )
        assert env_vars["METAPROC_GCP_SUBNETWORK"] == (
            "projects/test-project/regions/us-central1/subnetworks/test-subnet"
        )
        assert env_vars["METAPROC_GCP_FILESTORE_SERVER"] == "10.0.0.1"
        assert env_vars["METAPROC_GCP_FILESTORE_SHARE"] == "/test-share"
        assert env_vars["METAPROC_GCP_FILESTORE_MOUNT_PATH"] == "/mnt/test-filestore"
        assert env_vars["METAPROC_GCP_BOOT_DISK_GB"] == "123"
        assert env_vars["METAPROC_GCP_SECRET_GH_TOKEN"] == (
            "projects/test-project/secrets/gh-token/versions/latest"
        )
        assert json.loads(env_vars[SECRET_REFS_ENV])["GH_TOKEN"] == (
            "projects/test-project/secrets/gh-token/versions/latest"
        )
        assert not environment.secret_variables

    def test_orchestrator_forwards_claude_creds_secret(self):

        pytest.importorskip("google.cloud.batch_v1")

        gcp_config = GCPBatchConfig(
            project="test-project",
            region="us-central1",
            container_image="gcr.io/test/agent:latest",
            service_account_email="user@example.invalid",
            filestore_server="10.0.0.1",
        )
        config = OrchestratorDispatchConfig(
            gcp=gcp_config,
            process_spec_rel="example_plugin",
            variables={"RUN_ID": "test-run"},
            variant="claude-code-cli",
            poll_interval=0,
        )

        mock_client = MagicMock()
        mock_job = MagicMock()
        mock_job.name = "projects/p/locations/r/jobs/j"
        mock_job.status.state = JobStatus.State.SUCCEEDED
        mock_client.create_job.return_value = mock_job
        mock_client.get_job.return_value = mock_job

        secret_ref = "projects/test-project/secrets/claude-code-creds-ryan/versions/latest"
        with (
            patch("google.cloud.batch_v1.BatchServiceClient", return_value=mock_client),
            patch.dict(
                "os.environ",
                {"METAPROC_GCP_SECRET_CLAUDE_CREDS": secret_ref},
                clear=False,
            ),
        ):
            asyncio.run(dispatch_orchestrator(config))

        request = (
            mock_client.create_job.call_args.kwargs.get("request")
            or mock_client.create_job.call_args[0][0]
        )
        environment = request.job.task_groups[0].task_spec.runnables[1].environment
        env_vars = environment.variables

        # The resource-name var is forwarded as a plain env var so the
        # orchestrator can propagate it when dispatching workers.
        assert env_vars["METAPROC_GCP_SECRET_CLAUDE_CREDS"] == secret_ref
        assert json.loads(env_vars[SECRET_REFS_ENV])["CLAUDE_CODE_CREDS_JSON"] == secret_ref
        assert not environment.secret_variables

    def test_orchestrator_refuses_plaintext_claude_creds(self):

        gcp_config = GCPBatchConfig(
            project="test-project",
            region="us-central1",
            container_image="gcr.io/test/agent:latest",
            filestore_server="10.0.0.1",
        )
        config = OrchestratorDispatchConfig(
            gcp=gcp_config,
            process_spec_rel="example_plugin",
            variables={"RUN_ID": "test-run"},
            variant="claude-code-cli",
            poll_interval=0,
        )

        mock_client = MagicMock()
        with (
            patch("google.cloud.batch_v1.BatchServiceClient", return_value=mock_client),
            patch.dict(
                "os.environ",
                {"CLAUDE_CODE_CREDS_JSON": '{"claudeAiOauth": {}}'},
                clear=False,
            ),
            pytest.raises(RuntimeError, match="METAPROC_GCP_SECRET_CLAUDE_CREDS"),
        ):
            asyncio.run(dispatch_orchestrator(config))
        assert not mock_client.create_job.called

    def test_worker_dispatch_binds_claude_creds_secret(self):

        gcp_config = GCPBatchConfig(
            project="test-project",
            region="us-central1",
            container_image="gcr.io/test/agent:latest",
            service_account_email="user@example.invalid",
            filestore_server="10.0.0.1",
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

        vars_json = json.dumps({"RUN_ID": "test-run"})
        secret_ref = "projects/test-project/secrets/claude-code-creds-ryan/versions/2"

        with (
            patch("google.cloud.batch_v1.BatchServiceClient", return_value=mock_client),
            patch.dict(
                "os.environ",
                {"METAPROC_GCP_SECRET_CLAUDE_CREDS": secret_ref},
                clear=False,
            ),
        ):
            asyncio.run(
                _submit_workers(
                    partitions=[["AAPL"]],
                    context_partitions=[[{"TICKER": "AAPL"}]],
                    config=config,
                    step="mine",
                    process_spec_rel="example_plugin",
                    vars_json=vars_json,
                    run_dir=None,
                    out=MagicMock(),
                )
            )

        request = (
            mock_client.create_job.call_args.kwargs.get("request")
            or mock_client.create_job.call_args[0][0]
        )
        environment = request.job.task_groups[0].task_spec.runnables[1].environment
        assert (
            json.loads(environment.variables[SECRET_REFS_ENV])["CLAUDE_CODE_CREDS_JSON"]
            == secret_ref
        )
        assert not environment.secret_variables

    def test_worker_dispatch_forwards_artifact_env(self):

        gcp_config = GCPBatchConfig(
            project="test-project",
            region="us-central1",
            container_image="gcr.io/test/agent:latest",
            filestore_server="10.0.0.1",
        )
        config = WorkerDispatchConfig(
            gcp=gcp_config,
            num_workers=1,
        )

        mock_client = MagicMock()
        mock_job = MagicMock()
        mock_job.name = "projects/p/locations/r/jobs/j"
        mock_client.create_job.return_value = mock_job

        with (
            patch("google.cloud.batch_v1.BatchServiceClient", return_value=mock_client),
            patch.dict(
                "os.environ",
                {
                    "METAPROC_WHEEL_GCS": "gs://dispatch-bucket/wheels/metaproc.whl",
                    "METAPROC_WORKSPACE_GCS": "gs://dispatch-bucket/job/workspace.tar.gz",
                },
                clear=False,
            ),
        ):
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
        assert env_vars["METAPROC_WHEEL_GCS"] == "gs://dispatch-bucket/wheels/metaproc.whl"
        assert env_vars["METAPROC_WORKSPACE_GCS"] == "gs://dispatch-bucket/job/workspace.tar.gz"

    def test_worker_dispatch_refuses_plaintext_claude_creds(self):

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
        )
        vars_json = json.dumps({"RUN_ID": "test-run"})

        mock_client = MagicMock()
        with (
            patch("google.cloud.batch_v1.BatchServiceClient", return_value=mock_client),
            patch.dict(
                "os.environ",
                {"CLAUDE_CODE_CREDS_JSON": '{"claudeAiOauth": {}}'},
                clear=False,
            ),
            pytest.raises(RuntimeError, match="METAPROC_GCP_SECRET_CLAUDE_CREDS"),
        ):
            asyncio.run(
                _submit_workers(
                    partitions=[["AAPL"]],
                    context_partitions=[[{"TICKER": "AAPL"}]],
                    config=config,
                    step="mine",
                    process_spec_rel="example_plugin",
                    vars_json=vars_json,
                    run_dir=None,
                    out=MagicMock(),
                )
            )
