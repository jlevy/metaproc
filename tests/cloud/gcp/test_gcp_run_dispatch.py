"""Tests for cloud/gcp/gcp_run_dispatch.py and commands/gcp_run.py."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import typer
from click import unstyle
from typer.testing import CliRunner

from metaproc.cloud.gcp import gcp_run_dispatch
from metaproc.cloud.gcp.batch_backend import GCPBatchConfig
from metaproc.cloud.gcp.gcp_run_dispatch import (
    DispatchGcpRunOptions,
    build_gcp_run_job,
    dispatch_gcp_run,
)
from metaproc.cloud.gcp.secret_hydration import SECRET_REFS_ENV
from metaproc.commands import gcp_run as cmd_gcp_run


def _config(**overrides: Any) -> GCPBatchConfig:
    base = GCPBatchConfig(
        project="p",
        region="us-central1",
        container_image="us-central1-docker.pkg.dev/p/agent:latest",
        machine_type="e2-standard-4",
        service_account_email="user@example.invalid",
        network="projects/p/global/networks/default",
        subnetwork="projects/p/regions/us-central1/subnetworks/default",
        max_run_duration_s=3600,
    )
    return dataclasses.replace(base, **overrides) if overrides else base


# ── build_gcp_run_job ────────────────────────────────────────


class TestBuildGcpRunJob:
    def test_minimal_job_has_entrypoint_command_and_cmd_env(self):
        cfg = _config()
        opts = DispatchGcpRunOptions(config=cfg)
        argv = ["metaproc", "status", "run-1"]
        job_id, job = build_gcp_run_job(argv, opts)

        # Container runs the gcp_run_entrypoint script.
        runnable = job.task_groups[0].task_spec.runnables[0]
        assert runnable.container.image_uri == cfg.container_image
        assert runnable.container.entrypoint == "python"
        assert list(runnable.container.commands) == [
            "-m",
            "metaproc.cloud.gcp.gcp_run_entrypoint",
        ]
        # User cmd shipped via METAPROC_GCP_RUN_CMD as JSON argv.
        env = dict(runnable.environment.variables)
        assert env["METAPROC_GCP_RUN_CMD"] == json.dumps(argv)
        # Default job id has the gcprun prefix and a non-empty suffix.
        assert job_id.startswith("gcprun-")

    def test_empty_cmd_raises(self):
        cfg = _config()
        opts = DispatchGcpRunOptions(config=cfg)
        with pytest.raises(ValueError, match="cmd argv must be non-empty"):
            build_gcp_run_job([], opts)

    def test_single_host_process_dag_remains_one_batch_task(self) -> None:
        """A local-backend DAG is one command, not a second cloud scheduler."""
        cfg = _config(machine_type="n2-highmem-8")
        opts = DispatchGcpRunOptions(config=cfg)
        argv = [
            "metaproc",
            "run-process",
            "workflows/example.process.md",
            "--backend",
            "local",
            "--var",
            "RUN_ID=example-run",
        ]

        _, job = build_gcp_run_job(argv, opts)

        assert len(job.task_groups) == 1
        task_group = job.task_groups[0]
        assert task_group.task_count == 1
        assert task_group.parallelism == 1
        runnable = task_group.task_spec.runnables[0]
        env = dict(runnable.environment.variables)
        assert env["METAPROC_GCP_RUN_CMD"] == json.dumps(argv)
        assert job.allocation_policy.instances[0].policy.machine_type == "n2-highmem-8"

    def test_secret_job_requires_explicit_service_account(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "METAPROC_GCP_SECRET_CLAUDE_CREDS",
            "projects/p/secrets/claude/versions/latest",
        )
        opts = DispatchGcpRunOptions(config=_config(service_account_email=""))

        with pytest.raises(ValueError, match="METAPROC_GCP_SERVICE_ACCOUNT"):
            build_gcp_run_job(["echo", "x"], opts)

    def test_wheel_and_workspace_uris_flow_to_env(self):
        cfg = _config()
        opts = DispatchGcpRunOptions(
            config=cfg,
            wheel_gcs_uri="gs://b/gcp-run/wheels/metaproc-1.0-py3-none-any.whl",
            wheel_sha256="1" * 64,
            workspace_gcs_uri="gs://b/gcp-run/jobid/workspace.tar.gz",
            workspace_sha256="2" * 64,
            workspace_packages=("packages/example", "workflow"),
        )
        _, job = build_gcp_run_job(["echo", "x"], opts)
        env = dict(job.task_groups[0].task_spec.runnables[0].environment.variables)
        assert env["METAPROC_WHEEL_GCS"] == opts.wheel_gcs_uri
        assert env["METAPROC_WHEEL_SHA256"] == opts.wheel_sha256
        assert env["METAPROC_WORKSPACE_GCS"] == opts.workspace_gcs_uri
        assert env["METAPROC_WORKSPACE_SHA256"] == opts.workspace_sha256
        assert env["METAPROC_WORKSPACE_PACKAGES"] == "packages/example,workflow"

    def test_workspace_packages_require_workspace_artifact(self):
        opts = DispatchGcpRunOptions(
            config=_config(),
            workspace_packages=("packages/example",),
        )
        with pytest.raises(ValueError, match="workspace_packages requires"):
            build_gcp_run_job(["echo", "x"], opts)

    @pytest.mark.parametrize(
        "package_path",
        ["", ".", "../outside", "/absolute", "packages/example,packages/other"],
    )
    def test_workspace_package_paths_must_be_safe_for_env_transport(
        self, package_path: str
    ) -> None:
        opts = DispatchGcpRunOptions(
            config=_config(),
            workspace_gcs_uri="gs://b/workspace.tar.gz",
            workspace_sha256="2" * 64,
            workspace_packages=(package_path,),
        )

        with pytest.raises(ValueError, match="workspace package path"):
            build_gcp_run_job(["echo", "x"], opts)

    @pytest.mark.parametrize(
        "options",
        [
            DispatchGcpRunOptions(config=_config(), wheel_gcs_uri="gs://b/w.whl"),
            DispatchGcpRunOptions(config=_config(), wheel_sha256="1" * 64),
            DispatchGcpRunOptions(config=_config(), workspace_gcs_uri="gs://b/ws.tgz"),
            DispatchGcpRunOptions(config=_config(), workspace_sha256="2" * 64),
        ],
    )
    def test_artifact_uri_and_digest_must_be_paired(self, options: DispatchGcpRunOptions):
        with pytest.raises(ValueError, match="provided together"):
            build_gcp_run_job(["echo", "x"], options)

    def test_artifact_digest_must_be_sha256(self) -> None:
        options = DispatchGcpRunOptions(
            config=_config(), wheel_gcs_uri="gs://b/w.whl", wheel_sha256="not-a-digest"
        )
        with pytest.raises(ValueError, match="64 hexadecimal"):
            build_gcp_run_job(["echo", "x"], options)

    def test_runs_dir_propagates_when_set_on_config(self):
        cfg = _config(runs_dir="/mnt/filestore/runs")
        opts = DispatchGcpRunOptions(config=cfg)
        _, job = build_gcp_run_job(["echo", "x"], opts)
        env = dict(job.task_groups[0].task_spec.runnables[0].environment.variables)
        assert env["RUNS_DIR"] == "/mnt/filestore/runs"

    def test_filestore_config_uses_container_mount_path_for_runs_dir(self):
        # When the config has a filestore_server and a caller-local runs_dir,
        # the dispatched job must use the container mount path.
        cfg = _config(
            runs_dir="/tmp/local-runs",
            filestore_server="10.0.0.5",
            filestore_mount_path="/mnt/filestore",
        )
        opts = DispatchGcpRunOptions(config=cfg)
        _, job = build_gcp_run_job(["echo", "x"], opts)
        env = dict(job.task_groups[0].task_spec.runnables[1].environment.variables)
        assert env["RUNS_DIR"] == "/mnt/filestore/runs"

    def test_extra_env_layers_non_reserved_keys(self):
        cfg = _config()
        opts = DispatchGcpRunOptions(
            config=cfg,
            extra_env={"FOO": "bar", "ANOTHER": "baz"},
        )
        _, job = build_gcp_run_job(["echo", "x"], opts)
        env = dict(job.task_groups[0].task_spec.runnables[0].environment.variables)
        assert env["FOO"] == "bar"
        assert env["ANOTHER"] == "baz"

    def test_extra_env_reserved_key_raises(self):
        cfg = _config(runs_dir="/mnt/filestore/runs")
        opts = DispatchGcpRunOptions(
            config=cfg,
            extra_env={"FOO": "bar", "RUNS_DIR": "/override"},
        )
        with pytest.raises(ValueError, match="dispatcher-owned keys"):
            build_gcp_run_job(["echo", "x"], opts)

    def test_extra_env_rejects_registered_plaintext_credentials(self):
        opts = DispatchGcpRunOptions(
            config=_config(),
            extra_env={"GH_TOKEN": "plaintext"},
        )
        with pytest.raises(ValueError, match="cannot carry registered credentials"):
            build_gcp_run_job(["echo", "x"], opts)

    def test_extra_env_cannot_override_workspace_packages(self):
        opts = DispatchGcpRunOptions(
            config=_config(),
            extra_env={"METAPROC_WORKSPACE_PACKAGES": "packages/override"},
        )
        with pytest.raises(ValueError, match="dispatcher-owned keys"):
            build_gcp_run_job(["echo", "x"], opts)

    def test_secrets_resolved_from_env_registry(self, monkeypatch: pytest.MonkeyPatch):
        # Set the registry-side var so SecretRefSet resolves it.
        monkeypatch.setenv(
            "METAPROC_GCP_SECRET_CLAUDE_CREDS",
            "projects/p/secrets/claude/versions/latest",
        )
        monkeypatch.delenv("METAPROC_GCP_SECRET_GH_TOKEN", raising=False)
        cfg = _config()
        opts = DispatchGcpRunOptions(config=cfg)
        _, job = build_gcp_run_job(["echo", "x"], opts)
        environment = job.task_groups[0].task_spec.runnables[0].environment
        secrets = json.loads(environment.variables[SECRET_REFS_ENV])
        assert secrets["CLAUDE_CODE_CREDS_JSON"] == "projects/p/secrets/claude/versions/latest"
        assert "GH_TOKEN" not in secrets
        assert not environment.secret_variables

    def test_extra_secrets_override_registry(self, monkeypatch: pytest.MonkeyPatch):
        # Empty registry, caller provides a secret directly.
        monkeypatch.delenv("METAPROC_GCP_SECRET_CLAUDE_CREDS", raising=False)
        monkeypatch.delenv("METAPROC_GCP_SECRET_GH_TOKEN", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_CREDS_JSON", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        cfg = _config()
        opts = DispatchGcpRunOptions(
            config=cfg,
            extra_secrets={"MY_SECRET": "projects/p/secrets/foo/versions/1"},
        )
        _, job = build_gcp_run_job(["echo", "x"], opts)
        environment = job.task_groups[0].task_spec.runnables[0].environment
        secrets = json.loads(environment.variables[SECRET_REFS_ENV])
        assert secrets["MY_SECRET"] == "projects/p/secrets/foo/versions/1"
        assert not environment.secret_variables

    def test_filestore_volume_present_when_configured(self):
        cfg = _config(filestore_server="10.0.0.5")
        opts = DispatchGcpRunOptions(config=cfg)
        _, job = build_gcp_run_job(["echo", "x"], opts)
        runnables = job.task_groups[0].task_spec.runnables
        assert len(runnables) == 2
        assert runnables[0].display_name == "mount-filestore"
        container = runnables[1]
        assert any("/mnt/filestore" in v for v in container.container.volumes)

    def test_explicit_job_name_overrides_generated(self):
        cfg = _config()
        opts = DispatchGcpRunOptions(config=cfg, job_name="my-job-id")
        job_id, _ = build_gcp_run_job(["echo", "x"], opts)
        assert job_id == "my-job-id"

    def test_default_labels_include_gcp_run_role(self):
        cfg = _config()
        opts = DispatchGcpRunOptions(config=cfg)
        _, job = build_gcp_run_job(["echo", "x"], opts)
        assert job.labels["metaproc-role"] == "gcp-run"

    def test_machine_type_and_timeout_propagate(self):
        cfg = _config(machine_type="n2-highmem-8", max_run_duration_s=5400)
        opts = DispatchGcpRunOptions(config=cfg)
        _, job = build_gcp_run_job(["echo", "x"], opts)
        assert job.task_groups[0].task_spec.max_run_duration.seconds == 5400
        assert job.allocation_policy.instances[0].policy.machine_type == "n2-highmem-8"


# ── dispatch_gcp_run ────────────────────────────────────────


class TestDispatchGcpRun:
    def test_submits_job_via_batch_client_and_returns_resource_name(
        self, monkeypatch: pytest.MonkeyPatch
    ):

        created_job = MagicMock()
        created_job.name = "projects/p/locations/us-central1/jobs/my-job-id"

        client = MagicMock()
        client.create_job.return_value = created_job

        fake_batch_v1 = MagicMock()
        fake_batch_v1.BatchServiceClient.return_value = client
        fake_batch_v1.CreateJobRequest = MagicMock(side_effect=lambda **kw: kw)

        monkeypatch.setattr(gcp_run_dispatch, "_get_batch_v1", lambda: fake_batch_v1)

        cfg = _config()
        opts = DispatchGcpRunOptions(config=cfg, job_name="my-job-id")
        name = dispatch_gcp_run(["echo", "hi"], opts)
        assert name == "projects/p/locations/us-central1/jobs/my-job-id"

        # CreateJobRequest received the right parent and job_id.
        client.create_job.assert_called_once()
        request = client.create_job.call_args.args[0]
        assert request["parent"] == "projects/p/locations/us-central1"
        assert request["job_id"] == "my-job-id"


# ── commands/gcp_run.py CLI ────────────────────────────────────


class TestGcpRunCli:
    def test_parse_kv_pairs_happy_path(self):
        assert cmd_gcp_run._parse_kv_pairs(["A=1", "B=2"], "--env") == {"A": "1", "B": "2"}

    def test_parse_kv_pairs_missing_equals_raises(self):

        with pytest.raises(typer.BadParameter, match="--env expects KEY=VALUE"):
            cmd_gcp_run._parse_kv_pairs(["A1"], "--env")

    def test_parse_kv_pairs_empty_key_raises(self):

        with pytest.raises(typer.BadParameter, match="empty key"):
            cmd_gcp_run._parse_kv_pairs(["=v"], "--env")

    def test_expand_secret_ref_bare_name_gets_latest(self):
        out = cmd_gcp_run._expand_secret_ref("my-secret", project="p")
        assert out == "projects/p/secrets/my-secret/versions/latest"

    def test_expand_secret_ref_name_colon_version(self):
        out = cmd_gcp_run._expand_secret_ref("my-secret:3", project="p")
        assert out == "projects/p/secrets/my-secret/versions/3"

    def test_expand_secret_ref_full_path_unchanged(self):
        full = "projects/other/secrets/s/versions/7"
        assert cmd_gcp_run._expand_secret_ref(full, project="p") == full

    def test_expand_secret_ref_empty_secret_name_raises(self):

        with pytest.raises(typer.BadParameter, match="missing secret name"):
            cmd_gcp_run._expand_secret_ref(":5", project="p")

    def test_secret_shorthand_propagates_to_dispatch(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("METAPROC_GCP_PROJECT", "p")
        monkeypatch.setenv("METAPROC_GCP_CONTAINER_IMAGE", "us-central1-docker.pkg.dev/p/i:t")
        monkeypatch.setenv("METAPROC_GCS_BUCKET", "test-dispatch-bucket")
        monkeypatch.setenv("METAPROC_GCP_SERVICE_ACCOUNT", "batch@example.invalid")
        monkeypatch.delenv("METAPROC_GCP_FILESTORE_SERVER", raising=False)

        app = typer.Typer()
        app.command("run")(cmd_gcp_run.run_command)
        runner = CliRunner()

        captured: dict[str, Any] = {}

        def fake_dispatch(cmd: list[str], options: DispatchGcpRunOptions) -> str:
            captured["options"] = options
            return "projects/p/locations/us-central1/jobs/fake"

        with (
            patch.object(cmd_gcp_run, "build_wheel"),
            patch.object(cmd_gcp_run, "file_sha256", side_effect=["1" * 64, "2" * 64]),
            patch.object(
                cmd_gcp_run,
                "upload_wheel_to_gcs",
                return_value="gs://b/w.whl",
            ) as wheel_upload,
            patch.object(cmd_gcp_run, "package_workspace"),
            patch.object(
                cmd_gcp_run,
                "upload_workspace_to_gcs",
                return_value="gs://b/ws.tgz",
            ) as workspace_upload,
            patch.object(cmd_gcp_run, "dispatch_gcp_run", side_effect=fake_dispatch),
            patch.object(cmd_gcp_run, "tail_gcp_run_logs", return_value=0),
        ):
            result = runner.invoke(
                app,
                [
                    "--no-filestore",
                    "--secret",
                    "MY=bare-name",
                    "--secret",
                    "PINNED=pinned-name:5",
                    "echo",
                    "hi",
                ],
            )

        assert result.exit_code == 0, result.output
        opts = captured["options"]
        assert opts.extra_secrets["MY"] == "projects/p/secrets/bare-name/versions/latest"
        assert opts.extra_secrets["PINNED"] == "projects/p/secrets/pinned-name/versions/5"
        assert wheel_upload.call_args.kwargs["project"] == "p"
        assert workspace_upload.call_args.kwargs["project"] == "p"

    def test_secret_without_service_account_fails_before_artifact_shipping(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("METAPROC_GCP_PROJECT", "p")
        monkeypatch.setenv("METAPROC_GCP_CONTAINER_IMAGE", "example.invalid/agent:latest")
        monkeypatch.setenv("METAPROC_GCS_BUCKET", "test-dispatch-bucket")
        monkeypatch.delenv("METAPROC_GCP_SERVICE_ACCOUNT", raising=False)
        monkeypatch.delenv("METAPROC_GCP_FILESTORE_SERVER", raising=False)
        app = typer.Typer()
        app.command("run")(cmd_gcp_run.run_command)

        with patch.object(cmd_gcp_run, "_ship_artifacts") as ship_artifacts:
            result = CliRunner().invoke(
                app,
                [
                    "--no-filestore",
                    "--secret",
                    "API_TOKEN=api-token",
                    "echo",
                    "hi",
                ],
            )

        assert result.exit_code != 0
        assert "METAPROC_GCP_SERVICE_ACCOUNT" in result.output
        ship_artifacts.assert_not_called()

    def test_build_config_requires_project(self, monkeypatch: pytest.MonkeyPatch):

        monkeypatch.delenv("METAPROC_GCP_PROJECT", raising=False)
        with pytest.raises(typer.BadParameter, match="METAPROC_GCP_PROJECT"):
            cmd_gcp_run._build_config(
                image="img",
                machine_type="e2-standard-4",
                timeout=3600,
                spot=True,
                runs_dir="/mnt/filestore/runs",
                no_filestore=False,
            )

    def test_build_config_requires_image(self, monkeypatch: pytest.MonkeyPatch):

        monkeypatch.setenv("METAPROC_GCP_PROJECT", "p")
        monkeypatch.delenv("METAPROC_GCP_CONTAINER_IMAGE", raising=False)
        with pytest.raises(typer.BadParameter, match="--image"):
            cmd_gcp_run._build_config(
                image="",
                machine_type="e2-standard-4",
                timeout=3600,
                spot=True,
                runs_dir="/mnt/filestore/runs",
                no_filestore=False,
            )

    def test_env_rejects_reserved_keys(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("METAPROC_GCP_PROJECT", "p")
        monkeypatch.setenv("METAPROC_GCP_CONTAINER_IMAGE", "us-central1-docker.pkg.dev/p/i:t")
        monkeypatch.delenv("METAPROC_GCP_FILESTORE_SERVER", raising=False)

        app = typer.Typer()
        app.command("run")(cmd_gcp_run.run_command)
        runner = CliRunner()

        result = runner.invoke(
            app,
            [
                "--no-filestore",
                "--env",
                "RUNS_DIR=/override",
                "--dry-run",
                "echo",
                "hi",
            ],
        )
        assert result.exit_code != 0
        assert "RUNS_DIR" in result.output or "reserved" in result.output

    def test_artifact_shipping_requires_explicit_bucket(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("METAPROC_GCP_PROJECT", "p")
        monkeypatch.setenv("METAPROC_GCP_CONTAINER_IMAGE", "example.invalid/agent:latest")
        monkeypatch.delenv("METAPROC_GCP_FILESTORE_SERVER", raising=False)
        monkeypatch.delenv("METAPROC_GCS_BUCKET", raising=False)
        app = typer.Typer()
        app.command("run")(cmd_gcp_run.run_command)

        result = CliRunner().invoke(app, ["--no-filestore", "echo", "hi"])

        assert result.exit_code != 0
        assert "METAPROC_GCS_BUCKET" in result.output

    def test_build_config_no_filestore_clears_runs_dir(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("METAPROC_GCP_PROJECT", "p")
        monkeypatch.setenv("METAPROC_GCP_FILESTORE_SERVER", "10.0.0.5")
        cfg = cmd_gcp_run._build_config(
            image="img",
            machine_type="e2-standard-4",
            timeout=3600,
            spot=True,
            runs_dir="/mnt/filestore/runs",
            no_filestore=True,
        )
        assert cfg.filestore_server == ""
        assert cfg.runs_dir == ""

    def test_dry_run_prints_job_spec_and_skips_dispatch(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("METAPROC_GCP_PROJECT", "p")
        monkeypatch.setenv("METAPROC_GCP_CONTAINER_IMAGE", "us-central1-docker.pkg.dev/p/i:t")
        monkeypatch.delenv("METAPROC_GCP_FILESTORE_SERVER", raising=False)

        app = typer.Typer()
        app.command("run")(cmd_gcp_run.run_command)
        runner = CliRunner()

        # Default dry-run (no --no-wheel/--no-workspace): the artifact build/upload
        # helpers must NOT be invoked — a dry-run shouldn't hit `uv build` or GCS.
        with (
            patch.object(cmd_gcp_run, "build_wheel") as wheel_mock,
            patch.object(cmd_gcp_run, "upload_wheel_to_gcs") as wheel_up,
            patch.object(cmd_gcp_run, "package_workspace") as ws_mock,
            patch.object(cmd_gcp_run, "upload_workspace_to_gcs") as ws_up,
            patch.object(cmd_gcp_run, "dispatch_gcp_run") as dispatch_mock,
        ):
            result = runner.invoke(
                app,
                [
                    "--no-filestore",
                    "--dry-run",
                    "echo",
                    "hi",
                ],
            )

        assert result.exit_code == 0, result.output
        wheel_mock.assert_not_called()
        wheel_up.assert_not_called()
        ws_mock.assert_not_called()
        ws_up.assert_not_called()
        dispatch_mock.assert_not_called()
        # Output is JSON with a job spec; placeholder URIs flow into env vars.
        payload = json.loads(result.output)
        assert "job_id" in payload
        assert "job" in payload
        env_vars = payload["job"]["task_groups"][0]["task_spec"]["runnables"][0]["environment"][
            "variables"
        ]
        assert env_vars["METAPROC_WHEEL_GCS"].startswith("gs://")
        assert env_vars["METAPROC_WHEEL_SHA256"] == "0" * 64
        assert env_vars["METAPROC_WORKSPACE_GCS"].startswith("gs://")
        assert env_vars["METAPROC_WORKSPACE_SHA256"] == "0" * 64

    def test_run_invokes_dispatch_with_resolved_options(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("METAPROC_GCP_PROJECT", "p")
        monkeypatch.setenv("METAPROC_GCP_CONTAINER_IMAGE", "us-central1-docker.pkg.dev/p/i:t")
        monkeypatch.setenv("METAPROC_GCS_BUCKET", "test-dispatch-bucket")
        monkeypatch.setenv("METAPROC_GCP_SERVICE_ACCOUNT", "batch@example.invalid")
        monkeypatch.delenv("METAPROC_GCP_FILESTORE_SERVER", raising=False)

        app = typer.Typer()
        app.command("run")(cmd_gcp_run.run_command)
        runner = CliRunner()
        for relative in ("packages/example", "workflow"):
            package_dir = tmp_path / relative
            package_dir.mkdir(parents=True)
            (package_dir / "pyproject.toml").write_text("[project]\nname = 'example'\n")

        captured: dict[str, object] = {}

        def fake_dispatch(cmd: list[str], options: DispatchGcpRunOptions) -> str:
            captured["cmd"] = cmd
            captured["options"] = options
            return "projects/p/locations/us-central1/jobs/fake"

        with (
            patch.object(cmd_gcp_run, "build_wheel"),
            patch.object(cmd_gcp_run, "file_sha256", side_effect=["1" * 64, "2" * 64]),
            patch.object(cmd_gcp_run, "upload_wheel_to_gcs", return_value="gs://b/w.whl"),
            patch.object(cmd_gcp_run, "package_workspace"),
            patch.object(cmd_gcp_run, "upload_workspace_to_gcs", return_value="gs://b/ws.tgz"),
            patch.object(cmd_gcp_run, "find_repo_root", return_value=tmp_path),
            patch.object(cmd_gcp_run, "dispatch_gcp_run", side_effect=fake_dispatch),
            patch.object(cmd_gcp_run, "tail_gcp_run_logs", return_value=0) as tail_mock,
        ):
            result = runner.invoke(
                app,
                [
                    "--no-filestore",
                    "--env",
                    "FOO=bar",
                    "--secret",
                    "MY=projects/p/secrets/x/versions/1",
                    "--workspace-package",
                    "packages/example",
                    "--workspace-package",
                    "workflow",
                    "echo",
                    "hi",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "projects/p/locations/us-central1/jobs/fake" in result.output
        assert captured["cmd"] == ["echo", "hi"]
        opts = captured["options"]
        assert isinstance(opts, DispatchGcpRunOptions)
        assert opts.wheel_gcs_uri == "gs://b/w.whl"
        assert opts.wheel_sha256
        assert opts.workspace_gcs_uri == "gs://b/ws.tgz"
        assert opts.workspace_sha256
        assert opts.workspace_packages == ("packages/example", "workflow")
        assert opts.extra_env == {"FOO": "bar"}
        assert opts.extra_secrets == {"MY": "projects/p/secrets/x/versions/1"}
        # Blocking mode: tail was invoked with the resource name + project.
        tail_mock.assert_called_once()
        kwargs = tail_mock.call_args.kwargs
        assert kwargs["job_resource_name"] == "projects/p/locations/us-central1/jobs/fake"
        assert kwargs["project"] == "p"

    def test_workspace_package_rejects_no_workspace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("METAPROC_GCP_PROJECT", "p")
        monkeypatch.setenv("METAPROC_GCP_CONTAINER_IMAGE", "example.invalid/agent:latest")
        monkeypatch.delenv("METAPROC_GCP_FILESTORE_SERVER", raising=False)
        app = typer.Typer()
        app.command("run")(cmd_gcp_run.run_command)

        result = CliRunner().invoke(
            app,
            [
                "--no-filestore",
                "--no-workspace",
                "--workspace-package",
                "packages/example",
                "--dry-run",
                "echo",
                "hi",
            ],
            color=True,
        )

        assert result.exit_code != 0
        output = unstyle(result.output)
        assert "--workspace-package" in output
        assert "workspace shipping" in output

    def test_workspace_package_missing_pyproject_fails_before_artifact_build(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("METAPROC_GCP_PROJECT", "p")
        monkeypatch.setenv("METAPROC_GCP_CONTAINER_IMAGE", "example.invalid/agent:latest")
        monkeypatch.setenv("METAPROC_GCS_BUCKET", "test-dispatch-bucket")
        package_dir = tmp_path / "packages" / "example"
        package_dir.mkdir(parents=True)
        app = typer.Typer()
        app.command("run")(cmd_gcp_run.run_command)

        with (
            patch.object(cmd_gcp_run, "find_repo_root", return_value=tmp_path),
            patch.object(cmd_gcp_run, "build_wheel") as build_wheel_mock,
        ):
            result = CliRunner().invoke(
                app,
                ["--workspace-package", "packages/example", "echo", "hi"],
            )

        assert result.exit_code != 0
        assert "pyproject.toml" in unstyle(result.output)
        build_wheel_mock.assert_not_called()

    def test_sync_only_must_ship_the_workspace_package_before_artifact_build(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("METAPROC_GCP_PROJECT", "p")
        monkeypatch.setenv("METAPROC_GCP_CONTAINER_IMAGE", "example.invalid/agent:latest")
        monkeypatch.setenv("METAPROC_GCS_BUCKET", "test-dispatch-bucket")
        package_dir = tmp_path / "packages" / "example"
        package_dir.mkdir(parents=True)
        (package_dir / "pyproject.toml").write_text("[project]\nname = 'example'\n")
        (tmp_path / "docs").mkdir()
        app = typer.Typer()
        app.command("run")(cmd_gcp_run.run_command)

        with (
            patch.object(cmd_gcp_run, "find_repo_root", return_value=tmp_path),
            patch.object(cmd_gcp_run, "build_wheel") as build_wheel_mock,
        ):
            result = CliRunner().invoke(
                app,
                [
                    "--sync-only",
                    "docs",
                    "--workspace-package",
                    "packages/example",
                    "echo",
                    "hi",
                ],
            )

        assert result.exit_code != 0
        assert "--sync-only" in unstyle(result.output)
        build_wheel_mock.assert_not_called()

    def test_run_rejects_invalid_artifact_identity_before_build(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("METAPROC_GCP_PROJECT", "p")
        monkeypatch.setenv("METAPROC_GCP_CONTAINER_IMAGE", "example.invalid/agent:latest")
        monkeypatch.setenv("METAPROC_GCS_BUCKET", "test-dispatch-bucket")
        app = typer.Typer()
        app.command("run")(cmd_gcp_run.run_command)

        with patch.object(cmd_gcp_run, "_ship_artifacts") as ship_artifacts:
            result = CliRunner().invoke(
                app,
                ["--no-filestore", "--job-name", "../reuse", "echo", "hi"],
            )

        assert result.exit_code != 0
        assert "lowercase GCP-safe ID" in unstyle(result.output)
        ship_artifacts.assert_not_called()

    def test_detach_skips_tail_and_prints_log_url(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("METAPROC_GCP_PROJECT", "p")
        monkeypatch.setenv("METAPROC_GCP_CONTAINER_IMAGE", "us-central1-docker.pkg.dev/p/i:t")
        monkeypatch.setenv("METAPROC_GCS_BUCKET", "test-dispatch-bucket")
        monkeypatch.delenv("METAPROC_GCP_FILESTORE_SERVER", raising=False)

        app = typer.Typer()
        app.command("run")(cmd_gcp_run.run_command)
        runner = CliRunner()

        with (
            patch.object(cmd_gcp_run, "build_wheel"),
            patch.object(cmd_gcp_run, "file_sha256", side_effect=["1" * 64, "2" * 64]),
            patch.object(cmd_gcp_run, "upload_wheel_to_gcs", return_value="gs://b/w.whl"),
            patch.object(cmd_gcp_run, "package_workspace"),
            patch.object(cmd_gcp_run, "upload_workspace_to_gcs", return_value="gs://b/ws.tgz"),
            patch.object(
                cmd_gcp_run,
                "dispatch_gcp_run",
                return_value="projects/p/locations/us-central1/jobs/fake",
            ),
            patch.object(cmd_gcp_run, "tail_gcp_run_logs") as tail_mock,
        ):
            result = runner.invoke(app, ["--no-filestore", "--detach", "echo", "hi"])

        assert result.exit_code == 0, result.output
        tail_mock.assert_not_called()
        assert "projects/p/locations/us-central1/jobs/fake" in result.output
        assert "console.cloud.google.com/logs/query" in result.output

    def test_blocking_mode_propagates_nonzero_exit_code(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("METAPROC_GCP_PROJECT", "p")
        monkeypatch.setenv("METAPROC_GCP_CONTAINER_IMAGE", "us-central1-docker.pkg.dev/p/i:t")
        monkeypatch.setenv("METAPROC_GCS_BUCKET", "test-dispatch-bucket")
        monkeypatch.delenv("METAPROC_GCP_FILESTORE_SERVER", raising=False)

        app = typer.Typer()
        app.command("run")(cmd_gcp_run.run_command)
        runner = CliRunner()

        with (
            patch.object(cmd_gcp_run, "build_wheel"),
            patch.object(cmd_gcp_run, "file_sha256", side_effect=["1" * 64, "2" * 64]),
            patch.object(cmd_gcp_run, "upload_wheel_to_gcs", return_value="gs://b/w.whl"),
            patch.object(cmd_gcp_run, "package_workspace"),
            patch.object(cmd_gcp_run, "upload_workspace_to_gcs", return_value="gs://b/ws.tgz"),
            patch.object(
                cmd_gcp_run,
                "dispatch_gcp_run",
                return_value="projects/p/locations/us-central1/jobs/fake",
            ),
            patch.object(cmd_gcp_run, "tail_gcp_run_logs", return_value=130),
        ):
            result = runner.invoke(app, ["--no-filestore", "echo", "hi"])

        assert result.exit_code == 130, result.output
