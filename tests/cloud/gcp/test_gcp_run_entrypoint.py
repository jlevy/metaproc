"""Tests for cloud/gcp/gcp_run_entrypoint.py and bootstrap_gcp_run."""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import json as _json
import os
import tarfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from google.cloud.batch_v1.types import AllocationPolicy

from metaproc.adapters.codex import CODEX_CREDS_ENV_VAR
from metaproc.adapters.registry import ADAPTER_REGISTRY
from metaproc.cloud.gcp import container_bootstrap, gcp_run_entrypoint
from metaproc.cloud.gcp.batch_backend import GCPBatchConfig, create_single_task_job
from metaproc.cloud.gcp.secret_hydration import SECRET_REFS_ENV

# ── bootstrap_gcp_run ────────────────────────────────────────


class TestBootstrapGcpRun:
    def test_no_env_vars_is_noop(self, tmp_path: Path):
        with patch.object(container_bootstrap, "_run") as run_mock:
            work_dir = container_bootstrap.bootstrap_gcp_run(home=tmp_path, env={})
        run_mock.assert_not_called()
        assert work_dir == "/tmp"

    def test_wheel_install_downloads_and_uv_installs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        wheel_dir = tmp_path / "wheel-dir"
        monkeypatch.setattr(container_bootstrap, "GCP_RUN_WHEEL_DIR", str(wheel_dir))
        run_mock = MagicMock(return_value=0)
        monkeypatch.setattr(container_bootstrap, "_run", run_mock)
        wheel_sha256 = hashlib.sha256(b"wheel").hexdigest()

        def fake_download(_uri: str, dst: str) -> None:
            Path(dst).write_bytes(b"wheel")

        monkeypatch.setattr(container_bootstrap, "_download_from_gcs", fake_download)
        container_bootstrap.bootstrap_gcp_run(
            home=tmp_path,
            env={
                "METAPROC_WHEEL_GCS": "gs://b/gcp-run/wheels/metaproc-1.0-py3-none-any.whl",
                "METAPROC_WHEEL_SHA256": wheel_sha256,
            },
        )
        local_wheel = str(wheel_dir / "metaproc-1.0-py3-none-any.whl")
        assert not Path(local_wheel).exists()
        cmds = [call.args[0] for call in run_mock.call_args_list]
        # Replace only Metaproc itself. The image already contains the audited
        # dependency/extras closure, which may use per-package cutoff exceptions.
        assert [
            "uv",
            "pip",
            "install",
            "--python",
            "/opt/venv/bin/python",
            "--force-reinstall",
            "--no-deps",
            local_wheel,
        ] in cmds

    def test_wheel_install_rejects_non_gs_uri(self, tmp_path: Path):
        with pytest.raises(RuntimeError, match="must be a gs:// URI"):
            container_bootstrap.bootstrap_gcp_run(
                home=tmp_path,
                env={"METAPROC_WHEEL_GCS": "https://example.com/x.whl"},
            )

    def test_wheel_install_rejects_non_whl(self, tmp_path: Path):
        with pytest.raises(RuntimeError, match="Expected wheel name"):
            container_bootstrap.bootstrap_gcp_run(
                home=tmp_path,
                env={"METAPROC_WHEEL_GCS": "gs://b/wheels/metaproc.tar.gz"},
            )

    def test_wheel_download_failure_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        def fail_download(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("simulated 404")

        monkeypatch.setattr(container_bootstrap, "_download_from_gcs", fail_download)
        with pytest.raises(RuntimeError, match="download gs://"):
            container_bootstrap.bootstrap_gcp_run(
                home=tmp_path,
                env={
                    "METAPROC_WHEEL_GCS": "gs://b/wheels/metaproc-1.0.whl",
                    "METAPROC_WHEEL_SHA256": "0" * 64,
                },
            )

    @pytest.mark.parametrize(
        ("digest", "message"),
        [("", "METAPROC_WHEEL_SHA256 is required"), ("0" * 64, "SHA-256 mismatch")],
    )
    def test_wheel_install_requires_matching_sha256(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        digest: str,
        message: str,
    ) -> None:
        wheel_dir = tmp_path / "wheel-dir"
        monkeypatch.setattr(container_bootstrap, "GCP_RUN_WHEEL_DIR", str(wheel_dir))

        def fake_download(_uri: str, dst: str) -> None:
            Path(dst).write_bytes(b"wheel")

        monkeypatch.setattr(container_bootstrap, "_download_from_gcs", fake_download)
        with pytest.raises(RuntimeError, match=message):
            container_bootstrap.bootstrap_gcp_run(
                home=tmp_path,
                env={
                    "METAPROC_WHEEL_GCS": "gs://b/wheels/metaproc-1.0.whl",
                    "METAPROC_WHEEL_SHA256": digest,
                },
            )
        assert not (wheel_dir / "metaproc-1.0.whl").exists()

    def test_workspace_extract(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        # Build a real workspace tarball that the bootstrap will extract.
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "file.py").write_text("hello")
        tar_src = tmp_path / "ws.tar.gz"
        with tarfile.open(tar_src, "w:gz") as tar:
            tar.add(src_dir / "file.py", arcname="file.py")

        # Redirect /workspace and /tmp/metaproc-wheel to tmp_path so the test
        # does not need root permissions.
        workspace_dir = tmp_path / "workspace"
        wheel_dir = tmp_path / "wheel-dir"
        monkeypatch.setattr(container_bootstrap, "GCP_RUN_WORKSPACE_DIR", str(workspace_dir))
        monkeypatch.setattr(container_bootstrap, "GCP_RUN_WORKSPACE_ARCHIVE_DIR", str(wheel_dir))
        monkeypatch.setattr(container_bootstrap, "GCP_RUN_WHEEL_DIR", str(wheel_dir))

        # Stub _download_from_gcs to copy the local tarball to the destination.
        def fake_download(uri: str, dst: str) -> None:
            Path(dst).parent.mkdir(parents=True, exist_ok=True)
            Path(dst).write_bytes(Path(tar_src).read_bytes())

        monkeypatch.setattr(container_bootstrap, "_download_from_gcs", fake_download)

        work_dir = container_bootstrap.bootstrap_gcp_run(
            home=tmp_path,
            env={
                "METAPROC_WORKSPACE_GCS": "gs://b/gcp-run/job/workspace.tar.gz",
                "METAPROC_WORKSPACE_SHA256": hashlib.sha256(tar_src.read_bytes()).hexdigest(),
            },
        )
        assert work_dir == str(workspace_dir)
        assert (workspace_dir / "file.py").read_text() == "hello"
        # Downloaded tarball is staged outside the destination and removed.
        assert not (wheel_dir / "workspace.tar.gz").exists()

    def test_workspace_packages_install_into_baked_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src_dir = tmp_path / "src"
        package_dir = src_dir / "packages" / "example"
        package_dir.mkdir(parents=True)
        (package_dir / "pyproject.toml").write_text(
            "[project]\nname = 'example'\nversion = '0.1.0'\n"
        )
        tar_src = tmp_path / "ws.tar.gz"
        with tarfile.open(tar_src, "w:gz") as tar:
            tar.add(package_dir, arcname="packages/example")

        workspace_dir = tmp_path / "workspace"
        archive_dir = tmp_path / "archive"
        monkeypatch.setattr(container_bootstrap, "GCP_RUN_WORKSPACE_DIR", str(workspace_dir))
        monkeypatch.setattr(container_bootstrap, "GCP_RUN_WORKSPACE_ARCHIVE_DIR", str(archive_dir))
        monkeypatch.delenv("UV_PROJECT_ENVIRONMENT", raising=False)
        monkeypatch.delenv("UV_NO_SYNC", raising=False)

        def fake_download(_uri: str, dst: str) -> None:
            Path(dst).write_bytes(tar_src.read_bytes())

        run_mock = MagicMock(return_value=0)
        monkeypatch.setattr(container_bootstrap, "_download_from_gcs", fake_download)
        monkeypatch.setattr(container_bootstrap, "_run", run_mock)

        with patch.dict(os.environ):
            work_dir = container_bootstrap.bootstrap_gcp_run(
                home=tmp_path,
                env={
                    "METAPROC_WORKSPACE_GCS": "gs://b/gcp-run/job/workspace.tar.gz",
                    "METAPROC_WORKSPACE_SHA256": hashlib.sha256(tar_src.read_bytes()).hexdigest(),
                    "METAPROC_WORKSPACE_PACKAGES": "packages/example",
                },
            )

            assert work_dir == str(workspace_dir)
            run_mock.assert_called_once_with(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    "/opt/venv/bin/python",
                    "--no-deps",
                    "-e",
                    str(workspace_dir / "packages" / "example"),
                ]
            )
            assert os.environ["UV_PROJECT_ENVIRONMENT"] == "/opt/venv"
            assert os.environ["UV_NO_SYNC"] == "1"

        assert "UV_PROJECT_ENVIRONMENT" not in os.environ
        assert "UV_NO_SYNC" not in os.environ

    def test_workspace_packages_require_shipped_workspace(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match="requires METAPROC_WORKSPACE_GCS"):
            container_bootstrap.bootstrap_gcp_run(
                home=tmp_path,
                env={"METAPROC_WORKSPACE_PACKAGES": "packages/example"},
            )

    def test_workspace_rejects_unsafe_archive_member(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tar_src = tmp_path / "unsafe.tar.gz"
        with tarfile.open(tar_src, "w:gz") as archive:
            member = tarfile.TarInfo("../escape.txt")
            member.size = 7
            archive.addfile(member, io.BytesIO(b"payload"))
        workspace_dir = tmp_path / "workspace"
        archive_dir = tmp_path / "archive"
        monkeypatch.setattr(container_bootstrap, "GCP_RUN_WORKSPACE_DIR", str(workspace_dir))
        monkeypatch.setattr(container_bootstrap, "GCP_RUN_WORKSPACE_ARCHIVE_DIR", str(archive_dir))

        def fake_download(_uri: str, dst: str) -> None:
            Path(dst).write_bytes(tar_src.read_bytes())

        monkeypatch.setattr(container_bootstrap, "_download_from_gcs", fake_download)
        with pytest.raises(ValueError, match="unsafe archive member path"):
            container_bootstrap.bootstrap_gcp_run(
                home=tmp_path,
                env={
                    "METAPROC_WORKSPACE_GCS": "gs://b/job/workspace.tar.gz",
                    "METAPROC_WORKSPACE_SHA256": hashlib.sha256(tar_src.read_bytes()).hexdigest(),
                },
            )
        assert not (tmp_path / "escape.txt").exists()


# ── gcp_run_entrypoint.main ───────────────────────────────────


class TestGcpRunEntrypoint:
    def test_secret_hydration_failure_stops_before_bootstrap(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("METAPROC_GCP_RUN_CMD", json.dumps(["echo", "x"]))
        with (
            patch.object(
                gcp_run_entrypoint,
                "hydrate_secret_env",
                side_effect=RuntimeError("denied"),
            ),
            patch.object(gcp_run_entrypoint, "bootstrap_gcp_run") as bootstrap,
        ):
            assert gcp_run_entrypoint.main() == 1
        bootstrap.assert_not_called()

    def test_missing_cmd_returns_2(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("METAPROC_GCP_RUN_CMD", raising=False)
        rc = gcp_run_entrypoint.main()
        assert rc == 2

    def test_invalid_json_returns_2(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("METAPROC_GCP_RUN_CMD", "not-json")
        rc = gcp_run_entrypoint.main()
        assert rc == 2

    def test_non_list_returns_2(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("METAPROC_GCP_RUN_CMD", json.dumps({"cmd": "x"}))
        rc = gcp_run_entrypoint.main()
        assert rc == 2

    def test_empty_argv_returns_2(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("METAPROC_GCP_RUN_CMD", json.dumps([]))
        rc = gcp_run_entrypoint.main()
        assert rc == 2

    def test_execvp_invoked_with_argv(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        argv = ["echo", "hello"]
        monkeypatch.setenv("METAPROC_GCP_RUN_CMD", json.dumps(argv))

        # Bootstrap is a no-op when no GCS env vars set.
        with (
            patch.object(gcp_run_entrypoint, "ADAPTER_REGISTRY", {}),
            patch.object(os, "execvp") as execvp_mock,
            patch.object(os, "chdir") as chdir_mock,
        ):
            gcp_run_entrypoint.main()

        execvp_mock.assert_called_once_with("echo", ["echo", "hello"])
        # Falls back to /tmp when /workspace doesn't exist.
        chdir_args = chdir_mock.call_args.args[0]
        assert chdir_args in {"/tmp", "/workspace"}

    def test_bootstrap_failure_returns_1(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("METAPROC_GCP_RUN_CMD", json.dumps(["echo", "x"]))
        with patch.object(
            gcp_run_entrypoint, "bootstrap_gcp_run", side_effect=RuntimeError("boom")
        ):
            rc = gcp_run_entrypoint.main()
        assert rc == 1

    def test_adapter_bootstrap_loop_invokes_every_registered_adapter(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Regression coverage: every registered adapter's bootstrap() fires in the entrypoint.

        Without this invariant, adding a new adapter with a bootstrap hook
        (e.g. codex-cli's ~/.codex/auth.json materialization) would silently
        fail on GCP Batch workers because the entrypoint loop has to be
        touched by hand.
        """

        monkeypatch.setenv("METAPROC_GCP_RUN_CMD", json.dumps(["echo", "x"]))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        bootstrap_calls: dict[str, int] = {name: 0 for name in ADAPTER_REGISTRY}

        def _make_recorder(adapter_type: str):
            original_bootstrap = ADAPTER_REGISTRY[adapter_type].bootstrap

            def _record(home):
                bootstrap_calls[adapter_type] += 1
                return original_bootstrap(home)

            return _record

        with (
            patch.object(os, "execvp"),
            patch.object(os, "chdir"),
        ):
            for adapter_type in list(ADAPTER_REGISTRY):
                monkeypatch.setattr(
                    ADAPTER_REGISTRY[adapter_type],
                    "bootstrap",
                    _make_recorder(adapter_type),
                )
            gcp_run_entrypoint.main()

        # Every registered adapter was invoked exactly once — in particular
        # the codex-cli entry must be present and called.
        assert "codex-cli" in bootstrap_calls
        for adapter_type, count in bootstrap_calls.items():
            assert count == 1, f"{adapter_type!r} bootstrap called {count} times, expected 1"

    def test_codex_adapter_bootstrap_materializes_auth_json_in_entrypoint(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """End-to-end: CODEX_CREDS_JSON in the entrypoint env materializes auth.json."""

        monkeypatch.setenv("METAPROC_GCP_RUN_CMD", _json.dumps(["echo", "x"]))
        valid_oauth = _json.dumps(
            {
                "tokens": {
                    "auth_mode": "chatgpt",
                    "access_token": "fake",
                    "id_token": "fake",
                    "refresh_token": "fake",
                    "account_id": "fake",
                },
            }
        )
        monkeypatch.setenv(CODEX_CREDS_ENV_VAR, valid_oauth)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        with (
            patch.object(os, "execvp"),
            patch.object(os, "chdir"),
        ):
            gcp_run_entrypoint.main()

        auth_file = tmp_path / ".codex" / "auth.json"
        assert auth_file.is_file(), "codex adapter bootstrap should materialize auth.json"
        assert auth_file.read_text() == valid_oauth
        # env var must be unset so it doesn't leak to the user command.
        assert CODEX_CREDS_ENV_VAR not in os.environ


# ── create_single_task_job ────────────────────────────────────


class TestCreateSingleTaskJob:
    def _config(self, **overrides: Any) -> GCPBatchConfig:
        base = GCPBatchConfig(
            project="p",
            region="us-central1",
            container_image="us-central1-docker.pkg.dev/p/img:latest",
            machine_type="e2-standard-4",
            service_account_email="user@example.invalid",
            network="projects/p/global/networks/default",
            subnetwork="projects/p/regions/us-central1/subnetworks/default",
        )
        return dataclasses.replace(base, **overrides) if overrides else base

    def test_minimal_job_spec(self):
        cfg = self._config()
        job = create_single_task_job(
            config=cfg,
            env_vars={"FOO": "bar"},
            secret_refs={},
            container_command=["-m", "metaproc.cloud.gcp.gcp_run_entrypoint"],
        )
        assert job.task_groups[0].task_count == 1
        assert job.task_groups[0].parallelism == 1
        # Only the container runnable when filestore_server is unset.
        assert len(job.task_groups[0].task_spec.runnables) == 1
        runnable = job.task_groups[0].task_spec.runnables[0]
        assert runnable.container.image_uri == cfg.container_image
        assert runnable.container.entrypoint == "python"
        assert list(runnable.container.commands) == [
            "-m",
            "metaproc.cloud.gcp.gcp_run_entrypoint",
        ]
        assert dict(runnable.environment.variables) == {"FOO": "bar"}
        assert job.labels["metaproc-role"] == "gcp-run"

    def test_secret_refs_attached_without_batch_secret_variables(self):
        cfg = self._config()
        job = create_single_task_job(
            config=cfg,
            env_vars={"X": "y"},
            secret_refs={
                "CLAUDE_CODE_CREDS_JSON": "projects/p/secrets/claude-creds/versions/latest",
            },
            container_command=["-c", "echo hi"],
        )
        runnable = job.task_groups[0].task_spec.runnables[0]
        secrets = json.loads(runnable.environment.variables[SECRET_REFS_ENV])
        assert (
            secrets["CLAUDE_CODE_CREDS_JSON"] == "projects/p/secrets/claude-creds/versions/latest"
        )
        assert not runnable.environment.secret_variables

    def test_filestore_adds_mount_runnable(self):
        cfg = self._config(filestore_server="10.0.0.5")
        job = create_single_task_job(
            config=cfg,
            env_vars={},
            secret_refs={},
            container_command=["-V"],
        )
        # Two runnables: mount script + container.
        assert len(job.task_groups[0].task_spec.runnables) == 2
        mount = job.task_groups[0].task_spec.runnables[0]
        assert mount.display_name == "mount-filestore"
        container = job.task_groups[0].task_spec.runnables[1]
        assert any("/mnt/filestore" in v for v in container.container.volumes)

    def test_spot_default(self):
        cfg = self._config()
        job = create_single_task_job(
            config=cfg,
            env_vars={},
            secret_refs={},
            container_command=["-V"],
        )
        instance = job.allocation_policy.instances[0].policy
        assert instance.provisioning_model == AllocationPolicy.ProvisioningModel.SPOT

    def test_no_spot(self):
        cfg = self._config()
        job = create_single_task_job(
            config=cfg,
            env_vars={},
            secret_refs={},
            container_command=["-V"],
            spot=False,
        )
        instance = job.allocation_policy.instances[0].policy
        assert instance.provisioning_model == AllocationPolicy.ProvisioningModel.STANDARD

    def test_caller_labels_merged(self):
        cfg = self._config()
        job = create_single_task_job(
            config=cfg,
            env_vars={},
            secret_refs={},
            container_command=["-V"],
            job_labels={"metaproc-run-id": "run-x", "extra": "v"},
        )
        assert job.labels["metaproc-role"] == "gcp-run"
        assert job.labels["metaproc-run-id"] == "run-x"
        assert job.labels["extra"] == "v"
