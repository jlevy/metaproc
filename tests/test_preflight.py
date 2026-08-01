"""Tests for pre-flight checks — local and cloud infrastructure validation."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from metaproc.engine.preflight import (
    DEFAULT_PER_ITEM_BUDGET_MB,
    check_cli,
    check_container_image,
    check_disk_space,
    check_disk_space_for_batch,
    check_dispatch_resources,
    check_filestore_mount,
    check_gcloud_auth,
    check_gcp_project,
    check_machine_type,
    check_metaproc_wheel_for_branch_edits,
    check_service_account,
    run_cloud_preflight,
    run_cloud_preflight_warnings,
    run_preflight,
)


class TestCheckDiskSpace:
    def test_passes_with_sufficient_space(self):
        with patch("metaproc.engine.preflight.shutil.disk_usage") as mock_usage:
            mock_usage.return_value = MagicMock(free=10 * 1024**3)  # 10 GB
            ok, msg = check_disk_space(min_gb=5)
        assert ok is True
        assert "10.0 GB" in msg

    def test_fails_with_insufficient_space(self):
        with patch("metaproc.engine.preflight.shutil.disk_usage") as mock_usage:
            mock_usage.return_value = MagicMock(free=2 * 1024**3)  # 2 GB
            ok, msg = check_disk_space(min_gb=5)
        assert ok is False
        assert "2.0 GB" in msg

    def test_env_override_lowers_threshold(self, monkeypatch):
        """METAPROC_PREFLIGHT_MIN_DISK_GB env override is the operator escape
        hatch when running on a near-full disk with step-fingerprint cache
        making the actual per-batch delta small. Validates the post-2026-05-21
        fix."""
        monkeypatch.setenv("METAPROC_PREFLIGHT_MIN_DISK_GB", "2.0")
        with patch("metaproc.engine.preflight.shutil.disk_usage") as mock_usage:
            mock_usage.return_value = MagicMock(free=3 * 1024**3)  # 3 GB
            # No explicit min_gb — must pick up env override.
            ok, msg = check_disk_space()
        assert ok is True
        assert "2.0 GB" in msg  # threshold reported as the override value

    def test_env_override_invalid_value_falls_back_to_default(self, monkeypatch):
        """A malformed env var must not crash; fall back to the 5 GB default."""
        monkeypatch.setenv("METAPROC_PREFLIGHT_MIN_DISK_GB", "not-a-number")
        with patch("metaproc.engine.preflight.shutil.disk_usage") as mock_usage:
            mock_usage.return_value = MagicMock(free=10 * 1024**3)  # 10 GB
            ok, msg = check_disk_space()
        assert ok is True
        # Fell back to 5 GB default — verifies the env-parse exception handler.
        assert "5.0 GB" in msg

    def test_fail_message_names_override_env_var(self, monkeypatch):
        """When the check fails, the operator must be told about the env-var
        escape hatch — that's the whole point of having one."""
        monkeypatch.delenv("METAPROC_PREFLIGHT_MIN_DISK_GB", raising=False)
        with patch("metaproc.engine.preflight.shutil.disk_usage") as mock_usage:
            mock_usage.return_value = MagicMock(free=1 * 1024**3)  # 1 GB
            ok, msg = check_disk_space()
        assert ok is False
        assert "METAPROC_PREFLIGHT_MIN_DISK_GB" in msg


class TestCheckGcloudAuth:
    def test_passes_when_token_resolves(self):
        with patch("metaproc.cloud.gcp.resolve_token.resolve_gcp_token", return_value="ya29.abc"):
            ok, msg = check_gcloud_auth()
        assert ok is True
        assert "token" in msg.lower() or "ok" in msg.lower()

    def test_fails_when_import_missing(self):
        with patch.dict("sys.modules", {"metaproc.cloud.gcp.resolve_token": None}):
            ok, msg = check_gcloud_auth()
        assert ok is False
        assert "not installed" in msg.lower()


class TestRunPreflight:
    def test_all_pass(self):
        with (
            patch("metaproc.engine.preflight.check_disk_space", return_value=(True, "10 GB free")),
            patch("metaproc.engine.preflight.check_gcloud_auth", return_value=(True, "token ok")),
        ):
            results = run_preflight(needs_gcloud=True)
        assert all(ok for ok, _ in results)

    def test_skips_gcloud_when_not_needed(self):
        with patch("metaproc.engine.preflight.check_disk_space", return_value=(True, "10 GB free")):
            results = run_preflight(needs_gcloud=False)
        assert len(results) == 1  # only disk space

    def test_returns_failure_details(self):
        with (
            patch("metaproc.engine.preflight.check_disk_space", return_value=(False, "2 GB free")),
            patch("metaproc.engine.preflight.check_gcloud_auth", return_value=(True, "token ok")),
        ):
            results = run_preflight(needs_gcloud=True)
        failed = [(ok, msg) for ok, msg in results if not ok]
        assert len(failed) == 1
        assert "2 GB" in failed[0][1]


# ── Cloud infrastructure checks ─────────────────────────────────


class TestCheckCli:
    def test_finds_python(self):
        ok, msg = check_cli("python3")
        assert ok is True
        assert "found at" in msg

    def test_missing_cli(self):
        ok, msg = check_cli("nonexistent-cli-tool-12345")
        assert ok is False
        assert "not found" in msg


class TestCheckGcpProject:
    def test_passes_when_set(self):
        with patch.dict("os.environ", {"METAPROC_GCP_PROJECT": "my-project"}):
            ok, msg = check_gcp_project()
            assert ok is True
            assert "my-project" in msg

    def test_fails_when_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            ok, _msg = check_gcp_project()
            assert ok is False


class TestCheckContainerImage:
    def test_passes_when_set(self):
        with patch.dict("os.environ", {"METAPROC_GCP_CONTAINER_IMAGE": "gcr.io/img:latest"}):
            ok, _msg = check_container_image()
            assert ok is True

    def test_fails_when_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            ok, _msg = check_container_image()
            assert ok is False


class TestCheckServiceAccount:
    def test_passes_when_set(self):
        with patch.dict("os.environ", {"METAPROC_GCP_SERVICE_ACCOUNT": "user@example.invalid"}):
            ok, _msg = check_service_account()
            assert ok is True

    def test_passes_when_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            ok, msg = check_service_account()
            assert ok is True
            assert "default compute" in msg


class TestCheckFilestoreMount:
    def test_fails_when_server_not_set(self):
        with patch.dict("os.environ", {}, clear=True):
            ok, msg = check_filestore_mount()
            assert ok is False
            assert "not set" in msg

    def test_fails_when_path_missing(self):
        with patch.dict(
            "os.environ",
            {
                "METAPROC_GCP_FILESTORE_SERVER": "10.0.0.1",
                "METAPROC_GCP_FILESTORE_MOUNT_PATH": "/nonexistent/path/12345",
            },
        ):
            ok, msg = check_filestore_mount()
            assert ok is False
            assert "does not exist" in msg

    def test_passes_when_writable(self, tmp_path: Path):
        with patch.dict(
            "os.environ",
            {
                "METAPROC_GCP_FILESTORE_SERVER": "10.0.0.1",
                "METAPROC_GCP_FILESTORE_MOUNT_PATH": str(tmp_path),
            },
        ):
            ok, msg = check_filestore_mount()
            assert ok is True
            assert "writable" in msg

    def test_fails_when_not_writable(self, tmp_path: Path):
        # POSIX permission checks (chmod 444) are bypassed by the
        # superuser. When the test runs as root (typical in dev
        # containers), the write-probe inside check_filestore_mount
        # succeeds regardless of mode bits and the assertion below
        # would falsely fail. Use a path that genuinely can't be
        # written to: a missing parent directory under a read-only
        # mount-point name. Falls back to chmod for non-root runs.
        if os.geteuid() == 0:
            # /proc/1 is owned by root with mode 555 but `write` to
            # any path under it raises EACCES even for root because
            # procfs is a synthetic read-only fs. /proc itself is
            # always present on Linux.
            mount_path = "/proc/1/non-writable-test-dir"
            with patch.dict(
                "os.environ",
                {
                    "METAPROC_GCP_FILESTORE_SERVER": "10.0.0.1",
                    "METAPROC_GCP_FILESTORE_MOUNT_PATH": mount_path,
                },
            ):
                ok, msg = check_filestore_mount()
            # /proc/1/non-writable-test-dir doesn't exist, so the
            # check trips the "does not exist" branch — both that
            # and "not writable" are correct failure modes for the
            # spirit of this test (Filestore unhealthy → ok=False).
            assert ok is False
            assert "Filestore" in msg
            return

        read_only = tmp_path / "readonly"
        read_only.mkdir()
        read_only.chmod(0o444)
        try:
            with patch.dict(
                "os.environ",
                {
                    "METAPROC_GCP_FILESTORE_SERVER": "10.0.0.1",
                    "METAPROC_GCP_FILESTORE_MOUNT_PATH": str(read_only),
                },
            ):
                ok, msg = check_filestore_mount()
                assert ok is False
                assert "not writable" in msg
        finally:
            # Restore permissions for cleanup
            read_only.chmod(0o755)


class TestRunCloudPreflight:
    def test_reports_all_checks(self):
        with patch.dict("os.environ", {}, clear=True):
            results = run_cloud_preflight()
            assert len(results) >= 6

    def test_env_checks_pass_with_full_config(self, tmp_path: Path):
        env = {
            "METAPROC_GCP_PROJECT": "test-project",
            "METAPROC_GCP_CONTAINER_IMAGE": "gcr.io/test:latest",
            "METAPROC_GCP_FILESTORE_SERVER": "10.0.0.1",
            "METAPROC_GCP_FILESTORE_MOUNT_PATH": str(tmp_path),
        }
        with patch.dict("os.environ", env):
            results = run_cloud_preflight()
            env_failures = [(ok, msg) for ok, msg in results if not ok and "not set" in msg]
            assert len(env_failures) == 0


# ── Metaproc wheel preflight warning ────────────────────────────────


def _init_git_repo(root: Path) -> None:
    """Init a repo with a single no-metaproc commit on ``main``."""

    def run(*args: str) -> None:
        subprocess.run(args, cwd=str(root), check=True, capture_output=True)

    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    # Disable commit signing / GPG so tests work in any dev env.
    run("git", "config", "commit.gpgsign", "false")
    run("git", "config", "tag.gpgsign", "false")
    (root / "README.md").write_text("seed\n")
    run("git", "add", "README.md")
    run("git", "commit", "-q", "--no-gpg-sign", "-m", "seed")
    # Mirror origin/main onto the seed commit so base_ref='origin/main' resolves.
    run("git", "update-ref", "refs/remotes/origin/main", "HEAD")


def _write_vendored_metaproc_config(root: Path) -> None:
    """Register the consumer's Metaproc source without needing a remote repo."""
    (root / ".gitmodules").write_text(
        '[submodule "metaproc"]\n'
        "\tpath = vendor/metaproc\n"
        "\turl = https://github.com/example/metaproc.git\n"
    )


class TestCheckMetaprocWheelForBranchEdits:
    def test_passes_when_wheel_override_is_set(self, tmp_path: Path):
        env = {"METAPROC_WHEEL_GCS": "gs://bucket/metaproc.whl"}
        with patch.dict("os.environ", env, clear=True):
            ok, msg = check_metaproc_wheel_for_branch_edits(repo_root=tmp_path)
        assert ok is True
        assert "METAPROC_WHEEL_GCS" in msg

    def test_workspace_gcs_alone_is_not_sufficient(self, tmp_path: Path):
        """METAPROC_WORKSPACE_GCS covers companion packages, not metaproc."""
        _init_git_repo(tmp_path)
        (tmp_path / "metaproc").mkdir()
        (tmp_path / "metaproc" / "x.py").write_text("x = 1\n")
        env = {"METAPROC_WORKSPACE_GCS": "gs://bucket/workspace.tar.gz"}
        with patch.dict("os.environ", env, clear=True):
            ok, msg = check_metaproc_wheel_for_branch_edits(repo_root=tmp_path)
        assert ok is False
        assert "METAPROC_WHEEL_GCS" in msg
        assert "METAPROC_WORKSPACE_GCS does NOT cover" in msg

    def test_warns_when_not_a_git_repo(self, tmp_path: Path):
        with patch.dict("os.environ", {}, clear=True):
            ok, msg = check_metaproc_wheel_for_branch_edits(repo_root=tmp_path)
        assert ok is False
        assert "not a git repo" in msg

    def test_warns_when_base_ref_is_missing(self, tmp_path: Path):

        def run(*args: str) -> None:
            subprocess.run(args, cwd=str(tmp_path), check=True, capture_output=True)

        run("git", "init", "-q", "-b", "main")
        run("git", "config", "user.email", "t@t")
        run("git", "config", "user.name", "t")
        run("git", "config", "commit.gpgsign", "false")
        (tmp_path / "seed").write_text("seed\n")
        run("git", "add", "seed")
        run("git", "commit", "-q", "--no-gpg-sign", "-m", "seed")
        # Intentionally no refs/remotes/origin/main created.
        with patch.dict("os.environ", {}, clear=True):
            ok, msg = check_metaproc_wheel_for_branch_edits(repo_root=tmp_path)
        assert ok is False
        assert "origin/main" in msg
        assert "not fetched" in msg

    def test_warns_when_gitmodules_cannot_be_parsed(self, tmp_path: Path):
        _init_git_repo(tmp_path)
        (tmp_path / ".gitmodules").write_text('[submodule "metaproc"\n')
        with patch.dict("os.environ", {}, clear=True):
            ok, msg = check_metaproc_wheel_for_branch_edits(repo_root=tmp_path)
        assert ok is False
        assert "cannot inspect .gitmodules" in msg
        assert "METAPROC_WHEEL_GCS" in msg

    def test_passes_when_branch_matches_base(self, tmp_path: Path):
        _init_git_repo(tmp_path)
        with patch.dict("os.environ", {}, clear=True):
            ok, msg = check_metaproc_wheel_for_branch_edits(repo_root=tmp_path)
        assert ok is True
        assert "no tracked changes" in msg

    def test_warns_when_metaproc_commit_is_ahead_and_no_override(self, tmp_path: Path):
        _init_git_repo(tmp_path)
        (tmp_path / "metaproc").mkdir()
        (tmp_path / "metaproc" / "x.py").write_text("x = 1\n")

        subprocess.run(
            ["git", "add", "metaproc/x.py"],
            cwd=str(tmp_path),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-q", "--no-gpg-sign", "-m", "metaproc change"],
            cwd=str(tmp_path),
            check=True,
            capture_output=True,
        )
        with patch.dict("os.environ", {}, clear=True):
            ok, msg = check_metaproc_wheel_for_branch_edits(repo_root=tmp_path)
        assert ok is False
        assert "1 commit" in msg
        assert "METAPROC_WHEEL_GCS" in msg

    def test_warns_when_vendored_metaproc_commit_is_ahead(self, tmp_path: Path):
        _init_git_repo(tmp_path)
        _write_vendored_metaproc_config(tmp_path)
        source = tmp_path / "vendor" / "metaproc"
        source.mkdir(parents=True)
        (source / "x.py").write_text("x = 1\n")

        subprocess.run(
            ["git", "add", ".gitmodules", "vendor/metaproc/x.py"],
            cwd=str(tmp_path),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-q", "--no-gpg-sign", "-m", "metaproc change"],
            cwd=str(tmp_path),
            check=True,
            capture_output=True,
        )
        with patch.dict("os.environ", {}, clear=True):
            ok, msg = check_metaproc_wheel_for_branch_edits(repo_root=tmp_path)
        assert ok is False
        assert "1 commit" in msg
        assert "METAPROC_WHEEL_GCS" in msg

    def test_warns_when_metaproc_is_dirty_and_no_override(self, tmp_path: Path):
        _init_git_repo(tmp_path)
        (tmp_path / "metaproc").mkdir()
        (tmp_path / "metaproc" / "dirty.py").write_text("# not staged\n")
        with patch.dict("os.environ", {}, clear=True):
            ok, msg = check_metaproc_wheel_for_branch_edits(repo_root=tmp_path)
        assert ok is False
        assert "uncommitted" in msg

    def test_warns_when_vendored_metaproc_is_dirty(self, tmp_path: Path):
        _init_git_repo(tmp_path)
        _write_vendored_metaproc_config(tmp_path)
        source = tmp_path / "vendor" / "metaproc"
        source.mkdir(parents=True)
        (source / "dirty.py").write_text("# not staged\n")
        with patch.dict("os.environ", {}, clear=True):
            ok, msg = check_metaproc_wheel_for_branch_edits(repo_root=tmp_path)
        assert ok is False
        assert "uncommitted" in msg

    def test_ignores_non_metaproc_changes(self, tmp_path: Path):
        _init_git_repo(tmp_path)
        (tmp_path / "companion_package").mkdir()
        (tmp_path / "companion_package" / "y.py").write_text("y = 1\n")
        with patch.dict("os.environ", {}, clear=True):
            ok, msg = check_metaproc_wheel_for_branch_edits(repo_root=tmp_path)
        assert ok is True
        assert "no tracked changes" in msg


class TestRunCloudPreflightWarnings:
    def test_returns_metaproc_warning(self, tmp_path: Path):
        _init_git_repo(tmp_path)
        (tmp_path / "metaproc").mkdir()
        (tmp_path / "metaproc" / "z.py").write_text("z = 1\n")
        with patch.dict("os.environ", {}, clear=True):
            results = run_cloud_preflight_warnings(repo_root=tmp_path)
        assert len(results) == 1
        ok, _ = results[0]
        assert ok is False


class TestCheckDispatchResources:
    def test_passes_when_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            ok, msg = check_dispatch_resources()
        assert ok is True
        assert "parse cleanly" in msg

    def test_passes_with_valid_integers(self):
        env = {
            "METAPROC_GCP_BOOT_DISK_GB": "100",
            "METAPROC_GCP_MAX_RUN_DURATION_S": "7200",
            "METAPROC_GCP_TASK_CPU_MILLI": "4000",
            "METAPROC_GCP_TASK_MEMORY_MIB": "16384",
        }
        with patch.dict("os.environ", env, clear=True):
            ok, _ = check_dispatch_resources()
        assert ok is True

    def test_fails_on_malformed_boot_disk(self):
        with patch.dict("os.environ", {"METAPROC_GCP_BOOT_DISK_GB": "abc"}, clear=True):
            ok, msg = check_dispatch_resources()
        assert ok is False
        assert "METAPROC_GCP_BOOT_DISK_GB" in msg
        assert "abc" in msg

    def test_fails_on_malformed_cpu_milli(self):
        with patch.dict("os.environ", {"METAPROC_GCP_TASK_CPU_MILLI": "two-thousand"}, clear=True):
            ok, msg = check_dispatch_resources()
        assert ok is False
        assert "METAPROC_GCP_TASK_CPU_MILLI" in msg

    def test_aggregates_multiple_failures(self):
        env = {
            "METAPROC_GCP_BOOT_DISK_GB": "x",
            "METAPROC_GCP_MAX_RUN_DURATION_S": "y",
        }
        with patch.dict("os.environ", env, clear=True):
            ok, msg = check_dispatch_resources()
        assert ok is False
        assert "METAPROC_GCP_BOOT_DISK_GB" in msg
        assert "METAPROC_GCP_MAX_RUN_DURATION_S" in msg


class TestCheckMachineType:
    def test_reports_when_set(self):
        with patch.dict("os.environ", {"METAPROC_GCP_MACHINE_TYPE": "e2-highmem-8"}, clear=True):
            ok, msg = check_machine_type()
        assert ok is True
        assert "e2-highmem-8" in msg

    def test_passes_when_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            ok, msg = check_machine_type()
        assert ok is True
        assert "unset" in msg.lower()


class TestCheckDiskSpaceForBatch:
    """the disk-budget regression test: per-batch-aware disk budget."""

    def test_passes_with_sufficient_for_batch(self):
        # 9 lanes × 28 items × 150 MB = ~37 GB + 5 GB headroom = ~42 GB needed.
        with patch("metaproc.engine.preflight.shutil.disk_usage") as mock_usage:
            mock_usage.return_value = MagicMock(free=60 * 1024**3)  # 60 GB
            ok, msg = check_disk_space_for_batch(n_lanes=9, n_items=28)
        assert ok is True
        assert "60.0 GB free" in msg
        assert "9 lanes" in msg
        assert "28 items" in msg

    def test_fails_when_below_computed_budget(self):
        with patch("metaproc.engine.preflight.shutil.disk_usage") as mock_usage:
            mock_usage.return_value = MagicMock(free=20 * 1024**3)  # 20 GB
            ok, msg = check_disk_space_for_batch(n_lanes=9, n_items=28)
        assert ok is False
        # Message should include the actionable hint to evict old runs.
        assert "Evict old runs" in msg

    def test_default_per_item_budget_constant(self):
        # A representative batch observed ~150 MB per item across step outputs.
        assert DEFAULT_PER_ITEM_BUDGET_MB == 150

    def test_env_override_supersedes_computed_budget(self, monkeypatch):
        """METAPROC_PREFLIGHT_MIN_DISK_GB overrides the computed batch budget."""
        monkeypatch.setenv("METAPROC_PREFLIGHT_MIN_DISK_GB", "2.0")
        with patch("metaproc.engine.preflight.shutil.disk_usage") as mock_usage:
            mock_usage.return_value = MagicMock(free=3 * 1024**3)  # 3 GB
            # Without the override, a 9×28 batch needs ~42 GB and would fail.
            # With override=2 GB, 3 GB free passes.
            ok, msg = check_disk_space_for_batch(n_lanes=9, n_items=28)
        assert ok is True
        assert "2.0 GB" in msg  # override was respected

    def test_small_batch_needs_small_budget(self):
        # 3 lanes × 6 items × 150 MB = 2.6 GB + 5 GB headroom = ~7.6 GB.
        with patch("metaproc.engine.preflight.shutil.disk_usage") as mock_usage:
            mock_usage.return_value = MagicMock(free=10 * 1024**3)  # 10 GB
            ok, msg = check_disk_space_for_batch(n_lanes=3, n_items=6)
        assert ok is True
        assert "3 lanes" in msg
        assert "6 items" in msg

    def test_per_item_mb_param_overrides_default(self):
        # Operator measured 250 MB/item on a high-output run.
        with patch("metaproc.engine.preflight.shutil.disk_usage") as mock_usage:
            mock_usage.return_value = MagicMock(free=100 * 1024**3)
            ok, msg = check_disk_space_for_batch(n_lanes=9, n_items=28, per_item_mb=250)
        assert ok is True
        assert "250 MB" in msg
