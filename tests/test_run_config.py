"""Tests for run-config.yaml — immutable run identity and resume validation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from strif import atomic_output_file

from metaproc.commands.run_process import (
    _get_git_sha,
    _validate_run_config,
    _write_run_config,
)
from metaproc.errors import CLIError
from metaproc.io import read_yaml_file, to_yaml_string
from metaproc.paths import (
    DISPATCH_CONFIG_CHANGES_FILE,
    LOGS_DIR,
    RUN_CONFIG_FILE,
    RUN_LAYOUT_VERSION,
    STATE_DIR,
)


class TestWriteRunConfig:
    """Tests for _write_run_config — first write creates, second validates."""

    def test_creates_config_on_first_run(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-1" / "mine"
        run_dir.mkdir(parents=True)

        config_path = _write_run_config(
            run_dir,
            process_name="mine",
            process_path=Path("example_plugin/process/mine/mine.process.md"),
            run_id="run-1",
            variables={"RUN_ID": "run-1", "DATASET": "tech-500"},
            backend="gcp-worker",
            variant="pi-glm-5",
        )

        assert config_path.exists()
        assert config_path == run_dir / STATE_DIR / RUN_CONFIG_FILE

        data = read_yaml_file(config_path)
        assert data["process"] == "mine"
        assert data["run_id"] == "run-1"
        assert data["backend"] == "gcp-worker"
        assert data["variant"] == "pi-glm-5"
        assert data["variables"]["RUN_ID"] == "run-1"
        assert data["variables"]["DATASET"] == "tech-500"
        assert "created_at" in data
        assert "run_dir" in data
        assert data["metaproc_layout"] == RUN_LAYOUT_VERSION

    def test_no_variant_field_when_none(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-2" / "mine"
        run_dir.mkdir(parents=True)

        _write_run_config(
            run_dir,
            process_name="mine",
            process_path=Path("process/mine/mine.process.md"),
            run_id="run-2",
            variables={"RUN_ID": "run-2"},
            backend="local",
            variant=None,
        )

        data = read_yaml_file(run_dir / STATE_DIR / RUN_CONFIG_FILE)
        assert "variant" not in data

    def test_resumes_without_overwriting(self, tmp_path: Path) -> None:
        """Second call with same params succeeds and does not overwrite."""
        run_dir = tmp_path / "run-3" / "mine"
        run_dir.mkdir(parents=True)

        first_path = _write_run_config(
            run_dir,
            process_name="mine",
            process_path=Path("process/mine/mine.process.md"),
            run_id="run-3",
            variables={"RUN_ID": "run-3"},
            backend="local",
            variant=None,
        )
        first_data = read_yaml_file(first_path)

        # Second call — should validate, not overwrite.
        second_path = _write_run_config(
            run_dir,
            process_name="mine",
            process_path=Path("process/mine/mine.process.md"),
            run_id="run-3",
            variables={"RUN_ID": "run-3"},
            backend="local",
            variant=None,
        )
        second_data = read_yaml_file(second_path)

        assert first_data["created_at"] == second_data["created_at"]

    def test_resume_rejects_different_process(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-4" / "mine"
        run_dir.mkdir(parents=True)

        _write_run_config(
            run_dir,
            process_name="mine",
            process_path=Path("process/mine/mine.process.md"),
            run_id="run-4",
            variables={"RUN_ID": "run-4"},
            backend="local",
            variant=None,
        )

        with pytest.raises(CLIError, match="Resume mismatch.*process"):
            _write_run_config(
                run_dir,
                process_name="retro",
                process_path=Path("process/retro/retro.process.md"),
                run_id="run-4",
                variables={"RUN_ID": "run-4"},
                backend="local",
                variant=None,
            )

    def test_resume_rejects_different_run_dir(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-5" / "mine"
        run_dir.mkdir(parents=True)

        _write_run_config(
            run_dir,
            process_name="mine",
            process_path=Path("process/mine/mine.process.md"),
            run_id="run-5",
            variables={"RUN_ID": "run-5"},
            backend="local",
            variant=None,
        )

        # Simulate a different run_dir by patching the validation.
        config_path = run_dir / STATE_DIR / RUN_CONFIG_FILE
        different_dir = tmp_path / "other-dir" / "mine"
        with pytest.raises(CLIError, match="Resume mismatch.*run_dir"):
            _validate_run_config(
                config_path,
                process_name="mine",
                run_dir=different_dir,
            )

    def test_resume_accepts_legacy_filestore_mount_alias(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-5b" / "mine"
        run_dir.mkdir(parents=True)

        _write_run_config(
            run_dir,
            process_name="mine",
            process_path=Path("process/mine/mine.process.md"),
            run_id="run-5b",
            variables={"RUN_ID": "run-5b"},
            backend="local",
            variant=None,
        )

        config_path = run_dir / STATE_DIR / RUN_CONFIG_FILE
        data = read_yaml_file(config_path)
        data["run_dir"] = "/mnt/disks/filestore/runs/run-5b/mine"
        config_path.write_text(to_yaml_string(data))

        _validate_run_config(
            config_path,
            process_name="mine",
            run_dir=Path("/mnt/filestore/runs/run-5b/mine"),
        )

    def test_resume_accepts_local_mount_alias_for_same_run(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-5c" / "mine"
        run_dir.mkdir(parents=True)

        _write_run_config(
            run_dir,
            process_name="mine",
            process_path=Path("process/mine/mine.process.md"),
            run_id="run-5c",
            variables={"RUN_ID": "run-5c"},
            backend="local",
            variant=None,
        )

        config_path = run_dir / STATE_DIR / RUN_CONFIG_FILE
        data = read_yaml_file(config_path)
        data["run_dir"] = "/mnt/filestore/runs/run-5c/mine"
        config_path.write_text(to_yaml_string(data))

        _validate_run_config(
            config_path,
            process_name="mine",
            run_dir=Path("/workspace/user/mnt/filestore/runs/run-5c/mine"),
        )

    def test_resume_accepts_relocated_typed_partition(self, tmp_path: Path) -> None:
        run_id = "run_company-info-amzn-2026-07-31"
        run_dir = tmp_path / "machine-b" / run_id
        config_path = run_dir / STATE_DIR / RUN_CONFIG_FILE
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            to_yaml_string(
                {
                    "process": "company-info",
                    "run_id": run_id,
                    "run_dir": f"/machine-a/runs/{run_id}",
                }
            )
        )

        _validate_run_config(
            config_path,
            process_name="company-info",
            run_dir=run_dir,
        )

    def test_resume_rejects_different_typed_partition(self, tmp_path: Path) -> None:
        saved_run_id = "run_company-info-amzn-2026-07-31"
        current_run_id = "run_company-info-amzn-2026-07-31-revision-2"
        run_dir = tmp_path / current_run_id
        config_path = run_dir / STATE_DIR / RUN_CONFIG_FILE
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            to_yaml_string(
                {
                    "process": "company-info",
                    "run_id": saved_run_id,
                    "run_dir": f"/machine-a/runs/{saved_run_id}",
                }
            )
        )

        with pytest.raises(CLIError, match="Resume mismatch.*run_dir"):
            _validate_run_config(
                config_path,
                process_name="company-info",
                run_dir=run_dir,
            )

    def test_resume_rejects_unrelated_local_runs_directory(self, tmp_path: Path) -> None:
        # The directory name is deliberately untyped: a `run-`/`coh-` name would be a
        # relocatable typed partition, which is a different case covered above.
        run_dir = tmp_path / "mine-5d" / "mine"
        run_dir.mkdir(parents=True)

        _write_run_config(
            run_dir,
            process_name="mine",
            process_path=Path("process/mine/mine.process.md"),
            run_id="mine-5d",
            variables={"RUN_ID": "mine-5d"},
            backend="local",
            variant=None,
        )

        config_path = run_dir / STATE_DIR / RUN_CONFIG_FILE
        data = read_yaml_file(config_path)
        data["run_dir"] = "/mnt/filestore/runs/mine-5d/mine"
        config_path.write_text(to_yaml_string(data))

        with pytest.raises(CLIError, match="Resume mismatch.*run_dir"):
            _validate_run_config(
                config_path,
                process_name="mine",
                run_dir=Path("/tmp/runs/mine-5d/mine"),
            )

    def test_includes_git_sha(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-6" / "mine"
        run_dir.mkdir(parents=True)

        with patch(
            "metaproc.commands.run_process._get_git_sha",
            return_value="abc1234",
        ):
            _write_run_config(
                run_dir,
                process_name="mine",
                process_path=Path("process/mine/mine.process.md"),
                run_id="run-6",
                variables={"RUN_ID": "run-6"},
                backend="local",
                variant=None,
            )

        data = read_yaml_file(run_dir / STATE_DIR / RUN_CONFIG_FILE)
        assert data["git_sha"] == "abc1234"


class TestGetGitSha:
    def test_returns_sha_in_git_repo(self) -> None:
        sha = _get_git_sha()
        # We're in a git repo, so this should return something.
        assert len(sha) >= 7

    def test_returns_empty_on_failure(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert _get_git_sha() == ""


class TestValidateRunConfig:
    def test_raises_on_corrupt_file(self, tmp_path: Path) -> None:
        config_path = tmp_path / STATE_DIR / RUN_CONFIG_FILE
        config_path.parent.mkdir(parents=True)
        config_path.write_text("not valid yaml: [")

        with pytest.raises(Exception):  # noqa: B017
            _validate_run_config(config_path, process_name="mine", run_dir=tmp_path)

    def test_passes_on_matching_config(self, tmp_path: Path) -> None:
        """No error when config matches."""

        config_path = tmp_path / STATE_DIR / RUN_CONFIG_FILE
        config_path.parent.mkdir(parents=True)
        data = {
            "process": "mine",
            "run_dir": str(tmp_path),
            "run_id": "run-1",
        }
        with atomic_output_file(config_path) as tmp:
            Path(tmp).write_text(to_yaml_string(data))

        # Should not raise.
        _validate_run_config(config_path, process_name="mine", run_dir=tmp_path)


# ── Phase 3: auth + concurrency persistence ──


from metaproc.dispatch.auth_pool_flags import AuthPoolFlags  # noqa: E402


class TestAuthAndConcurrencyPersistence:
    """run-config v2: persist auth: + concurrency: blocks on first write,
    record dispatch_config_change events on resume.
    """

    def test_auth_block_persisted_on_first_write(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-1"
        run_dir.mkdir()
        flags = AuthPoolFlags(
            auth_account="claude-code-cli",
            auth_backend="local",
            auth_fallback_policy="same-provider",
            auth_policy="round-robin",
            auth_include_labels=("alt1", "alt2"),
        )
        config_path = _write_run_config(
            run_dir,
            process_name="predict",
            process_path=Path("p.md"),
            run_id="run-1",
            variables={},
            backend="local",
            variant=None,
            auth_flags=flags,
            max_concurrency=25,
        )
        data = read_yaml_file(config_path)
        assert isinstance(data, dict)
        assert "auth" in data
        assert data["auth"]["account"] == "claude-code-cli"
        assert data["auth"]["selection_policy"] == "round-robin"
        assert data["auth"]["include_labels"] == ["alt1", "alt2"]
        assert data["concurrency"] == {"initial": 25, "effective": 25}

    def test_no_auth_block_when_pool_dispatch_disabled(self, tmp_path: Path) -> None:
        # Operator didn't set --auth-account → no auth: block.
        run_dir = tmp_path / "run-1"
        run_dir.mkdir()
        config_path = _write_run_config(
            run_dir,
            process_name="predict",
            process_path=Path("p.md"),
            run_id="run-1",
            variables={},
            backend="local",
            variant=None,
            auth_flags=AuthPoolFlags(),
        )
        data = read_yaml_file(config_path)
        assert "auth" not in data

    def test_no_concurrency_block_when_max_unset(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-1"
        run_dir.mkdir()
        config_path = _write_run_config(
            run_dir,
            process_name="predict",
            process_path=Path("p.md"),
            run_id="run-1",
            variables={},
            backend="local",
            variant=None,
            max_concurrency=None,
        )
        data = read_yaml_file(config_path)
        assert "concurrency" not in data

    def test_resume_with_unchanged_flags_writes_no_change_event(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-1"
        run_dir.mkdir()
        flags = AuthPoolFlags(
            auth_account="claude-code-cli",
            auth_policy="round-robin",
            auth_include_labels=("alt1", "alt2"),
        )
        _write_run_config(
            run_dir,
            process_name="predict",
            process_path=Path("p.md"),
            run_id="run-1",
            variables={},
            backend="local",
            variant=None,
            auth_flags=flags,
            max_concurrency=25,
        )
        # Same flags on resume — no change event.
        _write_run_config(
            run_dir,
            process_name="predict",
            process_path=Path("p.md"),
            run_id="run-1",
            variables={},
            backend="local",
            variant=None,
            auth_flags=flags,
            max_concurrency=25,
        )
        changes_path = run_dir / LOGS_DIR / DISPATCH_CONFIG_CHANGES_FILE
        assert not changes_path.exists()

    def test_resume_with_changed_concurrency_writes_event(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-1"
        run_dir.mkdir()
        flags = AuthPoolFlags(
            auth_account="claude-code-cli",
            auth_policy="round-robin",
            auth_include_labels=("alt1", "alt2"),
        )
        _write_run_config(
            run_dir,
            process_name="predict",
            process_path=Path("p.md"),
            run_id="run-1",
            variables={},
            backend="local",
            variant=None,
            auth_flags=flags,
            max_concurrency=25,
        )
        _write_run_config(
            run_dir,
            process_name="predict",
            process_path=Path("p.md"),
            run_id="run-1",
            variables={},
            backend="local",
            variant=None,
            auth_flags=flags,
            max_concurrency=10,
        )

        changes_path = run_dir / LOGS_DIR / DISPATCH_CONFIG_CHANGES_FILE
        assert changes_path.exists()
        events = [json.loads(line) for line in changes_path.read_text().splitlines() if line]
        assert len(events) == 1
        change = events[0]
        assert change["event"] == "dispatch_config_change"
        fields = {c["field"] for c in change["changes"]}
        assert "max_concurrency" in fields
        # Original auth: block stays untouched on disk.
        config_path = run_dir / STATE_DIR / RUN_CONFIG_FILE
        data = read_yaml_file(config_path)
        assert data["concurrency"]["initial"] == 25  # original preserved

    def test_resume_with_changed_policy_writes_event(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-1"
        run_dir.mkdir()
        first_flags = AuthPoolFlags(
            auth_account="claude-code-cli",
            auth_policy="round-robin",
            auth_include_labels=("alt1", "alt2"),
        )
        second_flags = AuthPoolFlags(
            auth_account="claude-code-cli",
            auth_policy="priority-order",
            auth_include_labels=("alt1", "alt2"),
        )
        _write_run_config(
            run_dir,
            process_name="predict",
            process_path=Path("p.md"),
            run_id="run-1",
            variables={},
            backend="local",
            variant=None,
            auth_flags=first_flags,
        )
        _write_run_config(
            run_dir,
            process_name="predict",
            process_path=Path("p.md"),
            run_id="run-1",
            variables={},
            backend="local",
            variant=None,
            auth_flags=second_flags,
        )

        changes_path = run_dir / LOGS_DIR / DISPATCH_CONFIG_CHANGES_FILE
        assert changes_path.exists()
        events = [json.loads(line) for line in changes_path.read_text().splitlines() if line]
        assert len(events) == 1
        change_fields = events[0]["changes"][0]["diff"]
        assert "selection_policy" in change_fields
        assert change_fields["selection_policy"]["old"] == "round-robin"
        assert change_fields["selection_policy"]["new"] == "priority-order"
