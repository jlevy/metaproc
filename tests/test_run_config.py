"""Tests for run-config.yaml — run identity, resume validation, and recorded changes."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest
from strif import atomic_write_text

from metaproc.commands.run_process import (
    _apply_resume_config_changes,
    _get_git_sha,
    _RunConfigWrite,
    _validate_run_config,
    _write_run_config,
)
from metaproc.dispatch.auth_pool_flags import AuthPoolFlags
from metaproc.errors import CLIError
from metaproc.io import read_yaml_file, to_yaml_string
from metaproc.paths import (
    DISPATCH_CONFIG_CHANGES_FILE,
    LOGS_DIR,
    RUN_CONFIG_FILE,
    RUN_LAYOUT_VERSION,
    STATE_DIR,
)


def _resume_run_config(  # noqa: PLR0913
    run_dir: Path,
    *,
    process_name: str,
    variables: dict[str, str],
    variant: str | None = None,
    auth_flags: AuthPoolFlags | None = None,
    max_concurrency: int | None = None,
    step_variants: dict[str, str] | None = None,
    before_lease: Callable[[], None] | None = None,
) -> dict[str, tuple[str | None, str | None]]:
    """Resume the way ``run-process`` does: validate, then record once the lease is held.

    *before_lease* runs between the two, where ``run-process`` runs auth preflight and
    the ancestor check and another resume may still rewrite the config. Returns the
    changes recorded under the lease. The resume-only arguments of ``_write_run_config``
    (``process_path``, ``run_id``, ``backend``) are not compared, so fixed values stand
    in for them.
    """
    run_config = _write_run_config(
        run_dir,
        process_name=process_name,
        process_path=Path("process/mine/mine.process.md"),
        run_id="unused-on-resume",
        variables=variables,
        backend="local",
        variant=variant,
        auth_flags=auth_flags,
        max_concurrency=max_concurrency,
        step_variants=step_variants,
    )
    assert run_config == _RunConfigWrite(path=run_dir / STATE_DIR / RUN_CONFIG_FILE, resumed=True)
    if before_lease is not None:
        before_lease()
    return _apply_resume_config_changes(
        run_dir,
        run_config,
        process_name=process_name,
        variables=variables,
        step_variants=step_variants,
        auth_flags=auth_flags,
        max_concurrency=max_concurrency,
    )


def _events(run_dir: Path) -> list[dict[str, object]]:
    path = run_dir / LOGS_DIR / DISPATCH_CONFIG_CHANGES_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class TestWriteRunConfig:
    """Tests for _write_run_config — first write creates, second validates."""

    def test_creates_config_on_first_run(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-1" / "mine"
        run_dir.mkdir(parents=True)

        written = _write_run_config(
            run_dir,
            process_name="mine",
            process_path=Path("example_plugin/process/mine/mine.process.md"),
            run_id="run-1",
            variables={"RUN_ID": "run-1", "DATASET": "tech-500"},
            backend="gcp-worker",
            variant="pi-glm-5",
        )
        config_path = written.path

        assert not written.resumed
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
        ).path
        first_bytes = first_path.read_bytes()

        # Second call — should validate, not overwrite, and find nothing to record.
        assert _resume_run_config(run_dir, process_name="mine", variables={"RUN_ID": "run-3"}) == {}
        assert first_path.read_bytes() == first_bytes
        assert _events(run_dir) == []

    def test_resume_records_and_adopts_a_changed_variable(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-variable-change" / "mine"
        run_dir.mkdir(parents=True)

        created = _write_run_config(
            run_dir,
            process_name="mine",
            process_path=Path("process/mine/mine.process.md"),
            run_id="run-variable-change",
            variables={"RUN_ID": "run-variable-change", "DATASET": "original-dataset"},
            backend="local",
            variant="pi-glm-5",
            max_concurrency=4,
        )
        before = read_yaml_file(created.path)
        before_bytes = created.path.read_bytes()
        resumed_variables = {"RUN_ID": "run-variable-change", "DATASET": "replacement-dataset"}

        resumed = _write_run_config(
            run_dir,
            process_name="mine",
            process_path=Path("process/mine/mine.process.md"),
            run_id="run-variable-change",
            variables=resumed_variables,
            backend="local",
            variant="pi-glm-5",
            max_concurrency=4,
        )

        # Validation alone writes nothing: recording waits for the lease.
        assert resumed == _RunConfigWrite(path=created.path, resumed=True)
        assert resumed.path.read_bytes() == before_bytes
        assert _events(run_dir) == []

        applied = _apply_resume_config_changes(
            run_dir,
            resumed,
            process_name="mine",
            variables=resumed_variables,
            step_variants=None,
            auth_flags=None,
            max_concurrency=4,
        )

        assert applied == {"variables.DATASET": ("original-dataset", "replacement-dataset")}
        events = _events(run_dir)
        assert [event["event"] for event in events] == ["launch_config_change"]
        assert events[0]["changes"] == [
            {
                "field": "variables.DATASET",
                "diff": {"old": "original-dataset", "new": "replacement-dataset"},
            }
        ]
        # Only the variables are rewritten; every other field keeps its creation value.
        after = read_yaml_file(resumed.path)
        assert after["variables"] == {
            "RUN_ID": "run-variable-change",
            "DATASET": "replacement-dataset",
        }
        assert {key: value for key, value in after.items() if key != "variables"} == {
            key: value for key, value in before.items() if key != "variables"
        }

    def test_resume_records_a_changed_resolved_optional_default(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-default-change" / "mine"
        run_dir.mkdir(parents=True)

        _write_run_config(
            run_dir,
            process_name="mine",
            process_path=Path("process/mine/mine.process.md"),
            run_id="run-default-change",
            variables={"RUN_ID": "run-default-change", "OPTIONAL_MODE": "original-default"},
            backend="local",
            variant=None,
        )

        applied = _resume_run_config(
            run_dir,
            process_name="mine",
            variables={
                "RUN_ID": "run-default-change",
                "OPTIONAL_MODE": "replacement-default",
            },
        )

        assert applied == {"variables.OPTIONAL_MODE": ("original-default", "replacement-default")}
        config = read_yaml_file(run_dir / STATE_DIR / RUN_CONFIG_FILE)
        assert config["variables"]["OPTIONAL_MODE"] == "replacement-default"

    def test_the_changes_found_under_the_lease_are_the_ones_recorded(self, tmp_path: Path) -> None:
        """Another resume may rewrite the config between validation and the lease.

        What this resume records, rewrites, and reports is compared against the config as
        it stands under the lease, so the event carries that resume's value as ``old``.
        """
        run_dir = tmp_path / "run-concurrent" / "mine"
        run_dir.mkdir(parents=True)
        config_path = _write_run_config(
            run_dir,
            process_name="mine",
            process_path=Path("process/mine/mine.process.md"),
            run_id="run-concurrent",
            variables={"RUN_ID": "run-concurrent", "DATASET": "ds-a"},
            backend="local",
            variant=None,
        ).path

        def concurrent_resume(dataset: str) -> Callable[[], None]:
            def rewrite() -> None:
                data = read_yaml_file(config_path)
                data["variables"]["DATASET"] = dataset
                atomic_write_text(config_path, to_yaml_string(data))

            return rewrite

        # Validated against ds-a, recorded against the ds-b a concurrent resume wrote.
        applied = _resume_run_config(
            run_dir,
            process_name="mine",
            variables={"RUN_ID": "run-concurrent", "DATASET": "ds-c"},
            before_lease=concurrent_resume("ds-b"),
        )
        assert applied == {"variables.DATASET": ("ds-b", "ds-c")}
        assert [event["changes"] for event in _events(run_dir)] == [
            [{"field": "variables.DATASET", "diff": {"old": "ds-b", "new": "ds-c"}}]
        ]
        assert read_yaml_file(config_path)["variables"]["DATASET"] == "ds-c"

        # A resume that matched the config before the lease still records the change a
        # concurrent resume made, and restores the value it runs with.
        applied = _resume_run_config(
            run_dir,
            process_name="mine",
            variables={"RUN_ID": "run-concurrent", "DATASET": "ds-c"},
            before_lease=concurrent_resume("ds-d"),
        )
        assert applied == {"variables.DATASET": ("ds-d", "ds-c")}
        assert len(_events(run_dir)) == 2
        assert read_yaml_file(config_path)["variables"]["DATASET"] == "ds-c"

    def test_resume_records_step_variant_changes_and_rewrites_the_set(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-step-variants" / "mine"
        run_dir.mkdir(parents=True)

        def write(step_variants: dict[str, str] | None) -> dict[str, tuple[str | None, str | None]]:
            return _resume_run_config(
                run_dir,
                process_name="mine",
                variables={"RUN_ID": "run-step-variants"},
                step_variants=step_variants,
            )

        _write_run_config(
            run_dir,
            process_name="mine",
            process_path=Path("process/mine/mine.process.md"),
            run_id="run-step-variants",
            variables={"RUN_ID": "run-step-variants"},
            backend="local",
            variant=None,
            step_variants={"judge": "judge-a"},
        )
        config_path = run_dir / STATE_DIR / RUN_CONFIG_FILE
        assert write({"judge": "judge-a"}) == {}
        assert write({"judge": "judge-b", "draft": "draft-a"}) == {
            "step_variants.draft": (None, "draft-a"),
            "step_variants.judge": ("judge-a", "judge-b"),
        }
        assert read_yaml_file(config_path)["step_variants"] == {
            "draft": "draft-a",
            "judge": "judge-b",
        }
        # No overrides at all drops the field, as the first write omits an empty set.
        assert write(None) == {
            "step_variants.draft": ("draft-a", None),
            "step_variants.judge": ("judge-b", None),
        }
        assert "step_variants" not in read_yaml_file(config_path)

    def test_resume_of_a_moved_run_records_run_dir_and_keeps_its_creation_value(
        self, tmp_path: Path
    ) -> None:
        """The results projection rebases recorded paths from the creation ``run_dir``."""
        original = tmp_path / "before" / "run-moved"
        original.mkdir(parents=True)
        _write_run_config(
            original,
            process_name="mine",
            process_path=Path("process/mine/mine.process.md"),
            run_id="run-moved",
            variables={"RUN_ID": "run-moved"},
            backend="local",
            variant=None,
        )
        moved = tmp_path / "after" / "run-moved"
        shutil.copytree(original, moved)
        config_bytes = (moved / STATE_DIR / RUN_CONFIG_FILE).read_bytes()

        applied = _resume_run_config(moved, process_name="mine", variables={"RUN_ID": "run-moved"})

        assert applied == {"run_dir": (str(original), str(moved))}
        assert [event["event"] for event in _events(moved)] == ["launch_config_change"]
        assert (moved / STATE_DIR / RUN_CONFIG_FILE).read_bytes() == config_bytes

    def test_resume_accepts_equivalent_runs_dir_mount_alias(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-mount-alias" / "mine"
        run_dir.mkdir(parents=True)

        _write_run_config(
            run_dir,
            process_name="mine",
            process_path=Path("process/mine/mine.process.md"),
            run_id="run-mount-alias",
            variables={
                "RUN_ID": "run-mount-alias",
                "RUNS_DIR": "/mnt/disks/filestore/runs",
            },
            backend="gcp-orchestrator",
            variant=None,
        )

        applied = _resume_run_config(
            run_dir,
            process_name="mine",
            variables={
                "RUN_ID": "run-mount-alias",
                "RUNS_DIR": "/mnt/filestore/runs",
            },
        )

        # Two spellings of one mount are one value: no change, nothing rewritten.
        assert applied == {}
        config = read_yaml_file(run_dir / STATE_DIR / RUN_CONFIG_FILE)
        assert config["variables"]["RUNS_DIR"] == "/mnt/disks/filestore/runs"

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

        config_bytes = (run_dir / STATE_DIR / RUN_CONFIG_FILE).read_bytes()
        with pytest.raises(CLIError, match=r"Resume refused.*process.*run identity mine/run-4"):
            _write_run_config(
                run_dir,
                process_name="retro",
                process_path=Path("process/retro/retro.process.md"),
                run_id="run-4",
                variables={"RUN_ID": "run-4"},
                backend="local",
                variant=None,
            )
        assert (run_dir / STATE_DIR / RUN_CONFIG_FILE).read_bytes() == config_bytes
        assert not (run_dir / LOGS_DIR / DISPATCH_CONFIG_CHANGES_FILE).exists()

    def test_resume_records_different_run_dir(self, tmp_path: Path) -> None:
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
        changes = _validate_run_config(
            config_path,
            process_name="mine",
            run_dir=different_dir,
            variables={"RUN_ID": "run-5"},
        )
        assert changes == {"run_dir": (str(run_dir), str(different_dir))}

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
        atomic_write_text(config_path, to_yaml_string(data))

        changes = _validate_run_config(
            config_path,
            process_name="mine",
            run_dir=Path("/mnt/filestore/runs/run-5b/mine"),
            variables={"RUN_ID": "run-5b"},
        )
        assert changes == {}

    def test_resume_records_workstation_path_containing_filestore_alias(
        self, tmp_path: Path
    ) -> None:
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
        atomic_write_text(config_path, to_yaml_string(data))

        # Only the two root-level mount paths normalize; a workstation directory that
        # merely contains ``mnt/filestore`` is a different run directory.
        changes = _validate_run_config(
            config_path,
            process_name="mine",
            run_dir=Path("/workspace/user/mnt/filestore/runs/run-5c/mine"),
            variables={"RUN_ID": "run-5c"},
        )
        assert changes == {
            "run_dir": (
                "/mnt/filestore/runs/run-5c/mine",
                "/workspace/user/mnt/filestore/runs/run-5c/mine",
            )
        }

    def test_resume_records_unrelated_local_runs_directory(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-5d" / "mine"
        run_dir.mkdir(parents=True)

        _write_run_config(
            run_dir,
            process_name="mine",
            process_path=Path("process/mine/mine.process.md"),
            run_id="run-5d",
            variables={"RUN_ID": "run-5d"},
            backend="local",
            variant=None,
        )

        config_path = run_dir / STATE_DIR / RUN_CONFIG_FILE
        data = read_yaml_file(config_path)
        data["run_dir"] = "/mnt/filestore/runs/run-5d/mine"
        atomic_write_text(config_path, to_yaml_string(data))

        changes = _validate_run_config(
            config_path,
            process_name="mine",
            run_dir=Path("/tmp/runs/run-5d/mine"),
            variables={"RUN_ID": "run-5d"},
        )
        assert changes == {"run_dir": ("/mnt/filestore/runs/run-5d/mine", "/tmp/runs/run-5d/mine")}

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
        config_path.write_text("not valid yaml: [", encoding="utf-8")

        with pytest.raises(CLIError, match=r"Corrupt run-config\.yaml") as exc_info:
            _validate_run_config(
                config_path,
                process_name="mine",
                run_dir=tmp_path,
                variables={},
            )
        assert exc_info.value.__cause__ is not None

    def test_passes_on_matching_config(self, tmp_path: Path) -> None:
        """No error when config matches."""

        config_path = tmp_path / STATE_DIR / RUN_CONFIG_FILE
        data = {
            "process": "mine",
            "run_dir": str(tmp_path),
            "run_id": "run-1",
            "variables": {},
        }
        atomic_write_text(config_path, to_yaml_string(data), make_parents=True)

        # Should not raise, and nothing changed.
        changes = _validate_run_config(
            config_path,
            process_name="mine",
            run_dir=tmp_path,
            variables={},
        )
        assert changes == {}

    def test_rejects_non_string_variable_mapping(self, tmp_path: Path) -> None:
        config_path = tmp_path / STATE_DIR / RUN_CONFIG_FILE
        config_path.parent.mkdir(parents=True)
        data = {
            "process": "mine",
            "run_dir": str(tmp_path),
            "run_id": "run-1",
            "variables": {"COUNT": 3},
        }
        atomic_write_text(config_path, to_yaml_string(data))

        with pytest.raises(CLIError, match="variables must be a string-to-string mapping"):
            _validate_run_config(
                config_path,
                process_name="mine",
                run_dir=tmp_path,
                variables={"COUNT": "3"},
            )

    def test_rejects_explicit_null_variables(self, tmp_path: Path) -> None:
        config_path = tmp_path / STATE_DIR / RUN_CONFIG_FILE
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            to_yaml_string(
                {
                    "process": "mine",
                    "run_dir": str(tmp_path),
                    "run_id": "run-1",
                }
            )
            + "variables: null\n",
            encoding="utf-8",
        )

        with pytest.raises(CLIError, match="variables must be a string-to-string mapping"):
            _validate_run_config(
                config_path,
                process_name="mine",
                run_dir=tmp_path,
                variables={},
            )


# ── Phase 3: auth + concurrency persistence ──


class TestUnwritableChangeLog:
    """A resume that cannot append its change event refuses as a ``CLIError``."""

    @pytest.mark.parametrize(
        ("variables", "max_concurrency", "event"),
        [
            ({"DATASET": "ds-2"}, 25, "launch_config_change"),
            ({"DATASET": "ds-1"}, 10, "dispatch_config_change"),
        ],
        ids=["launch-config", "dispatch-config"],
    )
    def test_the_error_names_the_log_and_chains_the_cause(
        self, tmp_path: Path, variables: dict[str, str], max_concurrency: int, event: str
    ) -> None:
        run_dir = tmp_path / "run-1"
        run_dir.mkdir()
        _write_run_config(
            run_dir,
            process_name="mine",
            process_path=Path("p.md"),
            run_id="run-1",
            variables={"DATASET": "ds-1"},
            backend="local",
            variant=None,
            max_concurrency=25,
        )
        changes_path = run_dir / LOGS_DIR / DISPATCH_CONFIG_CHANGES_FILE
        # A directory where the log belongs cannot be opened for append.
        changes_path.mkdir(parents=True)

        with pytest.raises(CLIError, match=event) as exc_info:
            _resume_run_config(
                run_dir,
                process_name="mine",
                variables=variables,
                max_concurrency=max_concurrency,
            )

        assert str(changes_path) in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, OSError)


class TestAuthAndConcurrencyPersistence:
    """run-config v2: persist auth: + concurrency: blocks on first write,
    record dispatch_config_change events on resume, once the lease is held.
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
        ).path
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
        ).path
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
        ).path
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
        _resume_run_config(
            run_dir, process_name="predict", variables={}, auth_flags=flags, max_concurrency=25
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
        # Validation alone records nothing; the event is written once the lease is held.
        resumed = _write_run_config(
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
        assert not changes_path.exists()
        _apply_resume_config_changes(
            run_dir,
            resumed,
            process_name="predict",
            variables={},
            step_variants=None,
            auth_flags=flags,
            max_concurrency=10,
        )

        assert changes_path.exists()
        events = [
            json.loads(line)
            for line in changes_path.read_text(encoding="utf-8").splitlines()
            if line
        ]
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
        _resume_run_config(run_dir, process_name="predict", variables={}, auth_flags=second_flags)

        changes_path = run_dir / LOGS_DIR / DISPATCH_CONFIG_CHANGES_FILE
        assert changes_path.exists()
        events = [
            json.loads(line)
            for line in changes_path.read_text(encoding="utf-8").splitlines()
            if line
        ]
        assert len(events) == 1
        change_fields = events[0]["changes"][0]["diff"]
        assert "selection_policy" in change_fields
        assert change_fields["selection_policy"]["old"] == "round-robin"
        assert change_fields["selection_policy"]["new"] == "priority-order"
