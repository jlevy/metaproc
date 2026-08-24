"""Tests for container_bootstrap and entrypoint modules."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from metaproc.adapters.claude_code import CLAUDE_CREDS_ENV_VAR
from metaproc.cloud.gcp import container_bootstrap
from metaproc.cloud.gcp.container_bootstrap import (
    BootstrapResult,
    bootstrap_container,
)
from metaproc.cloud.gcp.orchestrator_entrypoint import main as orchestrator_main
from metaproc.cloud.gcp.worker_entrypoint import main as worker_main


def test_bootstrap_no_clone(tmp_path: Path) -> None:
    """Without METAPROC_RUN_BRANCH, bootstrap just creates work_dir."""
    env = {
        "METAPROC_RUN_BRANCH": "",
        "METAPROC_REPO_URL": "",
        "GH_TOKEN": "",
        "RUNS_DIR": "",
        "METAPROC_PI_MODELS_JSON": "",
    }
    with (
        patch.dict(os.environ, env, clear=False),
        patch("metaproc.cloud.gcp.container_bootstrap._run", return_value=0) as mock_run,
        patch("metaproc.cloud.gcp.container_bootstrap.WORK_DIR", str(tmp_path)),
    ):
        result = bootstrap_container()

    assert isinstance(result, BootstrapResult)
    assert result.work_dir == str(tmp_path)
    # Bootstrap does not mutate global Git configuration.
    mock_run.assert_not_called()


def test_bootstrap_pins_uv_to_baked_venv(tmp_path: Path) -> None:
    """Batch bootstrap should force all descendant ``uv run`` calls onto /opt/venv."""
    env = {
        "METAPROC_RUN_BRANCH": "",
        "METAPROC_REPO_URL": "",
        "GH_TOKEN": "",
        "RUNS_DIR": "",
        "METAPROC_PI_MODELS_JSON": "",
    }
    with (
        patch.dict(os.environ, env, clear=True),
        patch("metaproc.cloud.gcp.container_bootstrap._run", return_value=0),
        patch("metaproc.cloud.gcp.container_bootstrap.WORK_DIR", str(tmp_path)),
    ):
        bootstrap_container()

        assert os.environ["UV_PROJECT_ENVIRONMENT"] == "/opt/venv"
        assert os.environ["UV_NO_SYNC"] == "1"


def test_bootstrap_installs_shipped_metaproc_wheel(tmp_path: Path) -> None:
    env = {
        "METAPROC_RUN_BRANCH": "",
        "METAPROC_REPO_URL": "",
        "METAPROC_WHEEL_GCS": "gs://dispatch-bucket/wheels/metaproc.whl",
        "METAPROC_WHEEL_SHA256": "1" * 64,
        "GH_TOKEN": "",
        "RUNS_DIR": "",
        "METAPROC_PI_MODELS_JSON": "",
    }
    with (
        patch.dict(os.environ, env, clear=False),
        patch(
            "metaproc.cloud.gcp.container_bootstrap._install_wheel_from_gcs"
        ) as install_wheel_mock,
        patch("metaproc.cloud.gcp.container_bootstrap._run", return_value=0),
        patch("metaproc.cloud.gcp.container_bootstrap.WORK_DIR", str(tmp_path)),
    ):
        bootstrap_container()

    install_wheel_mock.assert_called_once_with("gs://dispatch-bucket/wheels/metaproc.whl", "1" * 64)


def test_bootstrap_logs_metaproc_source_image(tmp_path: Path, caplog) -> None:
    """With no shipped artifacts, bootstrap should record source=image."""
    env = {
        "METAPROC_RUN_BRANCH": "",
        "METAPROC_REPO_URL": "",
        "METAPROC_WHEEL_GCS": "",
        "METAPROC_WORKSPACE_GCS": "",
        "GH_TOKEN": "",
        "RUNS_DIR": "",
        "METAPROC_PI_MODELS_JSON": "",
    }
    with (
        caplog.at_level("INFO", logger="metaproc.cloud.gcp.container_bootstrap"),
        patch.dict(os.environ, env, clear=False),
        patch("metaproc.cloud.gcp.container_bootstrap._run", return_value=0),
        patch("metaproc.cloud.gcp.container_bootstrap.WORK_DIR", str(tmp_path)),
    ):
        bootstrap_container()

    assert any(
        "container_bootstrap: metaproc source=image" in record.message for record in caplog.records
    )


def test_bootstrap_logs_metaproc_source_wheel_gcs(tmp_path: Path, caplog) -> None:
    """When METAPROC_WHEEL_GCS is set, source=wheel_gcs is recorded with the URI."""
    env = {
        "METAPROC_RUN_BRANCH": "",
        "METAPROC_REPO_URL": "",
        "METAPROC_WHEEL_GCS": "gs://dispatch-bucket/wheels/metaproc.whl",
        "GH_TOKEN": "",
        "RUNS_DIR": "",
        "METAPROC_PI_MODELS_JSON": "",
    }
    with (
        caplog.at_level("INFO", logger="metaproc.cloud.gcp.container_bootstrap"),
        patch.dict(os.environ, env, clear=False),
        patch("metaproc.cloud.gcp.container_bootstrap._install_wheel_from_gcs"),
        patch("metaproc.cloud.gcp.container_bootstrap._run", return_value=0),
        patch("metaproc.cloud.gcp.container_bootstrap.WORK_DIR", str(tmp_path)),
    ):
        bootstrap_container()

    assert any(
        "container_bootstrap: metaproc source=wheel_gcs" in record.message
        and "gs://dispatch-bucket/wheels/metaproc.whl" in record.message
        for record in caplog.records
    )


def test_bootstrap_logs_image_source_when_only_workspace_gcs_set(tmp_path: Path, caplog) -> None:
    """METAPROC_WORKSPACE_GCS alone does not swap the baked metaproc.

    The extracted workspace is only used to install configured consumer
    packages. Metaproc itself stays at the image-baked version, so the source
    log must say ``source=image`` and log the workspace URI separately.
    """
    workspace_dir = tmp_path / "workspace"
    env = {
        "METAPROC_RUN_BRANCH": "",
        "METAPROC_REPO_URL": "",
        "METAPROC_WHEEL_GCS": "",
        "METAPROC_WORKSPACE_GCS": "gs://dispatch-bucket/job/workspace.tar.gz",
        "METAPROC_WORKSPACE_SHA256": "2" * 64,
        "METAPROC_WORKSPACE_PACKAGES": "workflow_plugin",
        "GH_TOKEN": "",
        "RUNS_DIR": "",
        "METAPROC_PI_MODELS_JSON": "",
    }
    with (
        caplog.at_level("INFO", logger="metaproc.cloud.gcp.container_bootstrap"),
        patch.dict(os.environ, env, clear=False),
        patch(
            "metaproc.cloud.gcp.container_bootstrap._extract_workspace_from_gcs",
            return_value=str(workspace_dir),
        ),
        patch("metaproc.cloud.gcp.container_bootstrap._install_workspace_packages"),
        patch("metaproc.cloud.gcp.container_bootstrap._run", return_value=0),
        patch("metaproc.cloud.gcp.container_bootstrap.WORK_DIR", str(tmp_path)),
    ):
        bootstrap_container()

    assert any(
        "container_bootstrap: metaproc source=image" in record.message for record in caplog.records
    ), "workspace_gcs alone must not claim source=workspace_gcs"
    assert any(
        "container_bootstrap: workspace_gcs=gs://dispatch-bucket/job/workspace.tar.gz"
        in record.message
        and "consumer workspace" in record.message
        for record in caplog.records
    ), "workspace_gcs URI should be logged separately from the Metaproc source"


def test_bootstrap_prefers_shipped_workspace_over_sparse_clone(tmp_path: Path) -> None:
    env = {
        "METAPROC_RUN_BRANCH": "main",
        "METAPROC_REPO_URL": "https://github.com/test/repo",
        "METAPROC_WORKSPACE_GCS": "gs://dispatch-bucket/job/workspace.tar.gz",
        "METAPROC_WORKSPACE_SHA256": "2" * 64,
        "METAPROC_WORKSPACE_PACKAGES": "workflow_plugin",
        "GH_TOKEN": "",
        "RUNS_DIR": "",
        "METAPROC_PI_MODELS_JSON": "",
    }
    workspace_dir = tmp_path / "workspace"
    with (
        patch.dict(os.environ, env, clear=False),
        patch(
            "metaproc.cloud.gcp.container_bootstrap._extract_workspace_from_gcs",
            return_value=str(workspace_dir),
        ) as extract_workspace_mock,
        patch(
            "metaproc.cloud.gcp.container_bootstrap._install_workspace_packages"
        ) as install_packages_mock,
        patch(
            "metaproc.cloud.gcp.container_bootstrap._bootstrap_sparse_clone"
        ) as sparse_clone_mock,
        patch("metaproc.cloud.gcp.container_bootstrap._run", return_value=0),
        patch("metaproc.cloud.gcp.container_bootstrap.WORK_DIR", str(tmp_path / "repo")),
    ):
        result = bootstrap_container()

    assert result.work_dir == str(workspace_dir)
    extract_workspace_mock.assert_called_once_with(
        "gs://dispatch-bucket/job/workspace.tar.gz", "2" * 64
    )
    install_packages_mock.assert_called_once_with(
        str(workspace_dir), ("workflow_plugin",), "workspace tarball"
    )
    sparse_clone_mock.assert_not_called()


def test_bootstrap_no_clone_falls_back_from_read_only_workspace(tmp_path: Path) -> None:
    """Read-only preferred work dirs fall back to the writable temp root."""
    preferred = tmp_path / "readonly" / "repo"
    fallback = tmp_path / "fallback" / "repo"
    real_makedirs = os.makedirs

    def fake_makedirs(path: str, exist_ok: bool = False) -> None:
        if os.fspath(path) == str(preferred):
            raise OSError(30, "Read-only file system")
        real_makedirs(path, exist_ok=exist_ok)

    env = {
        "METAPROC_RUN_BRANCH": "",
        "METAPROC_REPO_URL": "",
        "GH_TOKEN": "",
        "RUNS_DIR": "",
        "METAPROC_PI_MODELS_JSON": "",
    }
    with (
        patch.dict(os.environ, env, clear=False),
        patch("metaproc.cloud.gcp.container_bootstrap._run", return_value=0),
        patch("metaproc.cloud.gcp.container_bootstrap.os.makedirs", side_effect=fake_makedirs),
        patch("metaproc.cloud.gcp.container_bootstrap.WORK_DIR", str(preferred)),
        patch("metaproc.cloud.gcp.container_bootstrap.FALLBACK_WORK_DIR", str(fallback)),
    ):
        result = bootstrap_container()

    assert result.work_dir == str(fallback)
    assert fallback.is_dir()


def test_bootstrap_with_clone(tmp_path: Path) -> None:
    """With branch + repo, bootstrap clones and updates PYTHONPATH."""
    work_dir = str(tmp_path / "repo")
    metaproc_src = os.path.join(work_dir, "metaproc", "src")
    os.makedirs(metaproc_src)

    env = {
        "METAPROC_RUN_BRANCH": "main",
        "METAPROC_REPO_URL": "https://github.com/test/repo",
        "METAPROC_REPO_SPARSE_PATHS": "workflow_plugin,fixtures",
        "GH_TOKEN": "ghp_test123",
        "RUNS_DIR": str(tmp_path / "runs"),
        "METAPROC_PI_MODELS_JSON": "",
    }
    with (
        patch.dict(os.environ, env, clear=False),
        patch("metaproc.cloud.gcp.container_bootstrap._run", return_value=0) as mock_run,
        patch("metaproc.cloud.gcp.container_bootstrap.WORK_DIR", work_dir),
    ):
        result = bootstrap_container()

    assert result.work_dir == work_dir
    # Sparse checkout uses six Git calls. Credentials are process-scoped and are
    # never persisted through git config.
    assert mock_run.call_count == 6

    commands = [call.args[0] for call in mock_run.call_args_list]
    flattened = " ".join(arg for command in commands for arg in command)
    assert "ghp_test123" not in flattened
    assert "credential.helper" not in flattened
    fetch_call = next(
        call for call in mock_run.call_args_list if call.args[0][:2] == ["git", "fetch"]
    )
    fetch_env = fetch_call.kwargs["env"]
    assert fetch_env["METAPROC_GIT_TOKEN"] == "ghp_test123"
    assert fetch_env["GIT_TERMINAL_PROMPT"] == "0"
    assert fetch_env["GIT_CONFIG_KEY_0"] == "credential.helper"
    assert fetch_env["GIT_CONFIG_VALUE_0"] == ""
    askpass_path = Path(fetch_env["GIT_ASKPASS"])
    assert not askpass_path.exists(), "temporary askpass helper must be cleaned up"
    assert "GH_TOKEN" not in os.environ
    assert os.path.isdir(str(tmp_path / "runs"))


def test_run_failure_logging_never_exposes_secret(caplog: pytest.LogCaptureFixture) -> None:
    secret = "ghp_never_log_this"
    with (
        caplog.at_level("ERROR", logger="metaproc.cloud.gcp.container_bootstrap"),
        patch(
            "metaproc.cloud.gcp.container_bootstrap.subprocess.run",
            side_effect=RuntimeError(secret),
        ),
    ):
        assert (
            container_bootstrap._run(
                ["git", "fetch", f"https://x-access-token:{secret}@example.test/repo"],
                env={"METAPROC_GIT_TOKEN": secret},
            )
            == 1
        )

    assert secret not in caplog.text


def test_bootstrap_pi_models(tmp_path: Path) -> None:
    """Pi models JSON is written to ~/.pi/agent/models.json."""
    pi_json = '{"models": ["test"]}'
    pi_dir = tmp_path / "pi_home" / ".pi" / "agent"

    env = {
        "METAPROC_RUN_BRANCH": "",
        "METAPROC_REPO_URL": "",
        "GH_TOKEN": "",
        "RUNS_DIR": "",
        "METAPROC_PI_MODELS_JSON": pi_json,
        "HOME": str(tmp_path / "pi_home"),
    }
    with (
        patch.dict(os.environ, env, clear=False),
        patch("metaproc.cloud.gcp.container_bootstrap._run", return_value=0),
        patch("metaproc.cloud.gcp.container_bootstrap.WORK_DIR", str(tmp_path)),
    ):
        bootstrap_container()

    models_path = pi_dir / "models.json"
    assert models_path.read_text() == pi_json


def test_bootstrap_clone_failure(tmp_path: Path) -> None:
    """Clone failure raises RuntimeError."""
    env = {
        "METAPROC_RUN_BRANCH": "main",
        "METAPROC_REPO_URL": "https://github.com/test/repo",
        "GH_TOKEN": "",
        "RUNS_DIR": "",
        "METAPROC_PI_MODELS_JSON": "",
    }

    call_count = 0

    def mock_run_side_effect(
        cmd: list[str], cwd: str | None = None, *, env: object | None = None
    ) -> int:
        _ = env
        nonlocal call_count
        call_count += 1
        # First 2 calls are git config, 3rd is git init — fail it
        if call_count == 3:
            return 128
        return 0

    with (
        patch.dict(os.environ, env, clear=False),
        patch("metaproc.cloud.gcp.container_bootstrap._run", side_effect=mock_run_side_effect),
        patch("metaproc.cloud.gcp.container_bootstrap.WORK_DIR", str(tmp_path)),
    ):
        try:
            bootstrap_container()
            raised = False
        except RuntimeError:
            raised = True

    assert raised


def test_bootstrap_clone_failure_falls_back_to_bundled_repo(tmp_path: Path) -> None:
    """Repository clone failures fall back to an explicit bundled workspace."""
    work_dir = str(tmp_path / "repo")
    bundled_repo_dir = tmp_path / "bundle"
    bundled_package_dir = bundled_repo_dir / "workflow_plugin"
    bundled_package_dir.mkdir(parents=True)
    (bundled_package_dir / "pyproject.toml").write_text(
        "[project]\nname='workflow-plugin'\nversion='0.0.0'\n\n"
        "[tool.uv.sources]\nmetaproc = { workspace = true }\n"
    )

    env = {
        "METAPROC_RUN_BRANCH": "main",
        "METAPROC_REPO_URL": "https://github.com/test/repo",
        "METAPROC_WORKSPACE_PACKAGES": "workflow_plugin",
        "METAPROC_BUNDLED_REPO_DIR": str(bundled_repo_dir),
        "GH_TOKEN": "",
        "RUNS_DIR": "",
        "METAPROC_PI_MODELS_JSON": "",
    }

    def mock_run_side_effect(
        cmd: list[str], cwd: str | None = None, *, env: object | None = None
    ) -> int:
        _ = env
        if cmd[:2] == ["git", "fetch"]:
            return 128
        return 0

    with (
        patch.dict(os.environ, env, clear=False),
        patch(
            "metaproc.cloud.gcp.container_bootstrap._run", side_effect=mock_run_side_effect
        ) as mock_run,
        patch("metaproc.cloud.gcp.container_bootstrap.WORK_DIR", work_dir),
    ):
        result = bootstrap_container()

    assert result.work_dir == str(bundled_repo_dir)
    commands = [call.args[0] for call in mock_run.call_args_list]
    assert [
        "uv",
        "pip",
        "install",
        "--python",
        "/opt/venv/bin/python",
        "--no-deps",
        "-e",
        str(bundled_package_dir),
    ] in commands
    assert "[tool.uv.sources]" not in (bundled_package_dir / "pyproject.toml").read_text()


def test_worker_entrypoint_runs_adapter_bootstrap_materializes_claude_creds(
    tmp_path: Path,
) -> None:
    """Bootstrap hooks fire before run-parallel; Claude adapter writes
    ~/.claude/.credentials.json from CLAUDE_CODE_CREDS_JSON env var."""

    fake_home = tmp_path / "home"
    fake_home.mkdir()

    creds_payload = json.dumps(
        {
            "claudeAiOauth": {
                "accessToken": "sk-ant-oat01-FAKE",
                "refreshToken": "sk-ant-ort01-FAKE",
                "expiresAt": 1_800_000_000_000,
                "scopes": ["user:inference"],
            },
            "organizationUuid": "00000000-0000-0000-0000-000000000000",
        }
    )

    env = {
        "METAPROC_WORKER_ITEMS": "AAPL-2025Q3",
        "METAPROC_PROCESS_DIR": "example_plugin/process/mine",
        "METAPROC_STEP": "generate-record",
        "METAPROC_VARS": '{"RUN_ID":"run-1"}',
        CLAUDE_CREDS_ENV_VAR: creds_payload,
    }
    with (
        patch.dict(os.environ, env, clear=False),
        patch("metaproc.cloud.gcp.worker_entrypoint.Path.home", return_value=fake_home),
        patch(
            "metaproc.cloud.gcp.worker_entrypoint.bootstrap_container",
            return_value=BootstrapResult(work_dir=str(tmp_path)),
        ),
        patch("metaproc.cloud.gcp.worker_entrypoint._run", return_value=0),
    ):
        exit_code = worker_main()

    assert exit_code == 0

    creds_file = fake_home / ".claude" / ".credentials.json"
    assert creds_file.is_file(), "bootstrap hook did not materialize credentials file"
    assert creds_file.read_text() == creds_payload
    assert stat.S_IMODE(creds_file.stat().st_mode) == 0o600
    assert stat.S_IMODE((fake_home / ".claude").stat().st_mode) == 0o700
    assert CLAUDE_CREDS_ENV_VAR not in os.environ, (
        "bootstrap must pop the env var so it does not leak to child processes"
    )


def test_worker_entrypoint_passes_runs_dir_as_var(tmp_path: Path) -> None:
    runs_dir = str(tmp_path / "runs")
    env = {
        "METAPROC_WORKER_ITEMS": "AAPL-2025Q3",
        "METAPROC_PROCESS_DIR": "example_plugin/process/mine",
        "METAPROC_STEP": "generate-record",
        "METAPROC_VARS": '{"RUN_ID":"run-1"}',
        "RUNS_DIR": runs_dir,
    }
    with (
        patch.dict(os.environ, env, clear=False),
        patch(
            "metaproc.cloud.gcp.worker_entrypoint.bootstrap_container",
            return_value=BootstrapResult(work_dir=str(tmp_path)),
        ),
        patch("metaproc.cloud.gcp.worker_entrypoint._run", return_value=0) as mock_run,
    ):
        exit_code = worker_main()

    assert exit_code == 0
    cmd = mock_run.call_args.args[0]
    assert "--var" in cmd
    assert f"RUNS_DIR={runs_dir}" in cmd


def test_orchestrator_entrypoint_passes_runs_dir_as_var(tmp_path: Path) -> None:
    work_dir = tmp_path / "repo"
    dataset_dir = work_dir / "example_plugin" / "datasets"
    dataset_dir.mkdir(parents=True)
    (dataset_dir / "smoke.dataset.yml").write_text("dataset: smoke\n")
    runs_dir = str(tmp_path / "runs")

    env = {
        "METAPROC_PROCESS_DIR": "example_plugin/process/mine",
        "METAPROC_VARS": '{"RUN_ID":"run-1","DATASET":"smoke"}',
        "RUNS_DIR": runs_dir,
    }

    with (
        patch.dict(os.environ, env, clear=False),
        patch(
            "metaproc.cloud.gcp.orchestrator_entrypoint.bootstrap_container",
            return_value=BootstrapResult(work_dir=str(work_dir)),
        ),
        patch("metaproc.cloud.gcp.orchestrator_entrypoint._run", return_value=0) as mock_run,
    ):
        exit_code = orchestrator_main()

    assert exit_code == 0
    cmd = mock_run.call_args_list[-1].args[0]
    assert "--var" in cmd
    assert f"RUNS_DIR={runs_dir}" in cmd


def test_orchestrator_entrypoint_passes_num_workers_to_inner_run_process(tmp_path: Path) -> None:
    work_dir = tmp_path / "repo"
    dataset_dir = work_dir / "example_plugin" / "datasets"
    dataset_dir.mkdir(parents=True)
    (dataset_dir / "smoke.dataset.yml").write_text("dataset: smoke\n")

    env = {
        "METAPROC_PROCESS_DIR": "example_plugin/process/mine",
        "METAPROC_VARS": '{"RUN_ID":"run-1","DATASET":"smoke"}',
        "METAPROC_NUM_WORKERS": "2",
        "RUNS_DIR": str(tmp_path / "runs"),
    }

    with (
        patch.dict(os.environ, env, clear=False),
        patch(
            "metaproc.cloud.gcp.orchestrator_entrypoint.bootstrap_container",
            return_value=BootstrapResult(work_dir=str(work_dir)),
        ),
        patch("metaproc.cloud.gcp.orchestrator_entrypoint._run", return_value=0) as mock_run,
    ):
        exit_code = orchestrator_main()

    assert exit_code == 0
    cmd = mock_run.call_args_list[-1].args[0]
    assert "--num-workers" in cmd
    assert cmd[cmd.index("--num-workers") + 1] == "2"
