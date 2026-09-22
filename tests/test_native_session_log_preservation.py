"""Native agent session logs survive credential-slot teardown.

A pooled attempt runs its agent CLI with the CLI's config home scoped to a per-attempt
slot directory (``CODEX_HOME=<slot>/.codex``, ``CLAUDE_CONFIG_DIR=<slot>``). The CLI
writes its own session record there: Codex rollouts under ``.codex/sessions/`` and, when
persistence is enabled, Claude transcripts under ``projects/``. The slot is credential
bearing and is removed after every attempt, so these tests drive the dispatcher's real
preserve-then-teardown sequence (:func:`complete_slot`) with the real Codex and Claude
adapters and check what is left in the run's ``.logs/`` tree.

Every file here is synthetic: fake credential blobs, fake session records, fake paths.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from metaproc.adapters.claude_cli import ClaudeCodeCliAdapter
from metaproc.adapters.codex_cli import CodexCliAdapter
from metaproc.dispatch import slot_coordinator as slot_coordinator_module
from metaproc.dispatch.credential_pool import (
    ConcurrentModificationError,
    EntryState,
    PoolEntry,
    SelectionPolicy,
    SelectionStrategy,
    fingerprint_blob,
)
from metaproc.dispatch.pool_dispatch import PoolDispatchConfig, acquire_slot, complete_slot
from metaproc.dispatch.slot_coordinator import SlotCoordinator, SlotLease

_CODEX_SECRET = "synthetic-codex-access-token-must-not-be-copied"
_CLAUDE_SECRET = "synthetic-claude-access-token-must-not-be-copied"
_CODEX_BLOB = f'{{"tokens": {{"auth_mode": "chatgpt", "access_token": "{_CODEX_SECRET}"}}}}'
_CLAUDE_BLOB = f'{{"claudeAiOauth": {{"accessToken": "{_CLAUDE_SECRET}"}}}}'

_SESSION_STEM = "predict_AAPL_2026-09-14T10-00-00"
_ROLLOUT = "rollout-2026-09-14T10-00-00-00000000-0000-4000-8000-000000000001"
_CLAUDE_PROJECT = "-synthetic-workspace-project"
_CLAUDE_SESSION = "00000000-0000-4000-8000-000000000002"


@dataclass
class _InMemoryPool:
    entries: dict[tuple[str, str], PoolEntry] = field(default_factory=dict)
    counter: int = 0

    def get_entry(self, adapter: str, label: str) -> PoolEntry:
        key = (adapter, label)
        if key not in self.entries:
            msg = f"no pool entry for {adapter}/{label}"
            raise KeyError(msg)
        return self.entries[key]

    def list_entries(self, adapter: str | None = None) -> list[PoolEntry]:
        return [e for (a, _label), e in self.entries.items() if adapter is None or a == adapter]

    def upsert_entry(
        self,
        adapter: str,
        label: str,
        *,
        blob: str | None,
        state: EntryState | None,
        expected_etag: str | None = None,
    ) -> str:
        key = (adapter, label)
        existing = self.entries.get(key)
        if expected_etag is not None and existing is not None and existing.etag != expected_etag:
            raise ConcurrentModificationError(f"stale {expected_etag} != {existing.etag}")
        self.counter += 1
        new_blob = blob if blob is not None else (existing.blob if existing else "")
        new_state = (
            state
            if state is not None
            else (existing.state if existing else EntryState(status="active", fp=""))
        )
        etag = f"e-{self.counter}"
        self.entries[key] = PoolEntry(
            adapter=adapter, label=label, blob=new_blob, state=new_state, etag=etag
        )
        return etag

    def delete_entry(self, adapter: str, label: str) -> None:
        self.entries.pop((adapter, label), None)


@dataclass(frozen=True)
class _Attempt:
    config: PoolDispatchConfig
    lease: SlotLease
    session_log: Path


def _lease_attempt(tmp_path: Path, adapter_name: str, blob: str) -> _Attempt:
    adapter = {"codex-cli": CodexCliAdapter(), "claude-code-cli": ClaudeCodeCliAdapter()}[
        adapter_name
    ]
    pool = _InMemoryPool()
    pool.upsert_entry(
        adapter_name,
        "synthetic-label",
        blob=blob,
        state=EntryState(status="active", fp=fingerprint_blob(blob)),
    )
    runs_dir = tmp_path / "runs"
    config = PoolDispatchConfig(
        coordinator=SlotCoordinator(pool, adapter_registry={adapter_name: adapter}),
        adapter=adapter_name,
        runs_dir=runs_dir,
        run_id="run-1",
        step="predict",
        strategy=SelectionStrategy(SelectionPolicy.PRIORITY_ORDER, ("synthetic-label",)),
    )
    session_log = (
        runs_dir / "run-1" / ".logs" / "tasks" / "predict" / "AAPL" / f"{_SESSION_STEM}.jsonl"
    )
    session_log.parent.mkdir(parents=True)
    session_log.write_text('{"type": "captured-stdout"}\n')
    lease = acquire_slot(config, item="AAPL", attempt=1, session_log_path=session_log)
    return _Attempt(config=config, lease=lease, session_log=session_log)


def _complete(attempt: _Attempt, *, error_str: str | None = None) -> None:
    complete_slot(
        attempt.config,
        attempt.lease,
        error_str=error_str,
        session_log_path=attempt.session_log,
        retry_count=0,
        retry_exclude=[],
    )


def _write(path: Path, data: bytes | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")
    return path


def _write_codex_rollouts(slot_dir: Path) -> None:
    day = slot_dir / ".codex" / "sessions" / "2026" / "09" / "14"
    _write(day / f"{_ROLLOUT}.jsonl", '{"type": "session_meta", "synthetic": true}\n')
    _write(day / f"{_ROLLOUT}.jsonl.zst", b"\x28\xb5\x2f\xfdsynthetic-zstd-frame")


def _write_claude_transcripts(slot_dir: Path) -> None:
    project = slot_dir / "projects" / _CLAUDE_PROJECT
    _write(project / f"{_CLAUDE_SESSION}.jsonl", '{"type": "assistant", "synthetic": true}\n')
    subagents = project / _CLAUDE_SESSION / "subagents"
    _write(subagents / "agent-x.jsonl", '{"type": "assistant", "subagent": true}\n')
    _write(subagents / "agent-x.meta.json", '{"agentType": "synthetic"}\n')


def _run_logs_dir(attempt: _Attempt) -> Path:
    return attempt.config.runs_dir / "run-1" / ".logs"


def _preserved(attempt: _Attempt, log_set_name: str) -> Path:
    """Return the isolated native-log destination for one captured task log."""
    logs_dir = _run_logs_dir(attempt)
    relative_parent = attempt.session_log.parent.relative_to(logs_dir / "tasks")
    return logs_dir / "native" / relative_parent / f"{_SESSION_STEM}.{log_set_name}"


class TestCodexRolloutsSurviveTeardown:
    def test_rollouts_are_copied_into_the_native_log_namespace(self, tmp_path: Path) -> None:
        attempt = _lease_attempt(tmp_path, "codex-cli", _CODEX_BLOB)
        _write_codex_rollouts(attempt.lease.slot_dir)

        _complete(attempt)

        assert not attempt.lease.slot_dir.exists()
        preserved = _preserved(attempt, "codex-sessions")
        day = preserved / "2026" / "09" / "14"
        assert (day / f"{_ROLLOUT}.jsonl").read_text() == (
            '{"type": "session_meta", "synthetic": true}\n'
        )
        assert (day / f"{_ROLLOUT}.jsonl.zst").read_bytes() == (
            b"\x28\xb5\x2f\xfdsynthetic-zstd-frame"
        )


class TestClaudeTranscriptsSurviveTeardown:
    @pytest.mark.parametrize("error_str", [None, "exit code 1"])
    def test_transcripts_and_subagents_are_copied_into_the_native_log_namespace(
        self, tmp_path: Path, error_str: str | None
    ) -> None:
        attempt = _lease_attempt(tmp_path, "claude-code-cli", _CLAUDE_BLOB)
        _write_claude_transcripts(attempt.lease.slot_dir)

        _complete(attempt, error_str=error_str)

        assert not attempt.lease.slot_dir.exists()
        project = _preserved(attempt, "claude-projects") / _CLAUDE_PROJECT
        assert (project / f"{_CLAUDE_SESSION}.jsonl").read_text() == (
            '{"type": "assistant", "synthetic": true}\n'
        )
        subagents = project / _CLAUDE_SESSION / "subagents"
        assert (subagents / "agent-x.jsonl").read_text() == (
            '{"type": "assistant", "subagent": true}\n'
        )
        assert (subagents / "agent-x.meta.json").read_text() == '{"agentType": "synthetic"}\n'


class TestCredentialsNeverReachLogs:
    def test_codex_credentials_and_symlinks_are_not_copied(self, tmp_path: Path) -> None:
        attempt = _lease_attempt(tmp_path, "codex-cli", _CODEX_BLOB)
        slot = attempt.lease.slot_dir
        assert _CODEX_SECRET in (slot / ".codex" / "auth.json").read_text()
        assert (slot / ".codex" / "config.toml").is_file()
        _write_codex_rollouts(slot)
        day = slot / ".codex" / "sessions" / "2026" / "09" / "14"
        # A session-shaped name that resolves to the credential must not be followed.
        (day / "rollout-link.jsonl").symlink_to(slot / ".codex" / "auth.json")
        # Credential-bearing files that sit beside, not inside, the session tree.
        _write(slot / ".codex" / "history.jsonl", f'{{"note": "{_CODEX_SECRET}"}}\n')

        _complete(attempt)

        _assert_no_secret_or_symlink(_run_logs_dir(attempt), _CODEX_SECRET)
        preserved = _preserved(attempt, "codex-sessions")
        assert sorted(p.name for p in (preserved / "2026" / "09" / "14").iterdir()) == [
            f"{_ROLLOUT}.jsonl",
            f"{_ROLLOUT}.jsonl.zst",
        ]

    def test_claude_credentials_and_symlinks_are_not_copied(self, tmp_path: Path) -> None:
        attempt = _lease_attempt(tmp_path, "claude-code-cli", _CLAUDE_BLOB)
        slot = attempt.lease.slot_dir
        assert _CLAUDE_SECRET in (slot / ".credentials.json").read_text()
        _write_claude_transcripts(slot)
        project = slot / "projects" / _CLAUDE_PROJECT
        (project / "linked.jsonl").symlink_to(slot / ".credentials.json")
        _write(slot / "outside" / "secret.jsonl", f'{{"token": "{_CLAUDE_SECRET}"}}\n')
        (project / "escape").symlink_to(slot / "outside", target_is_directory=True)

        _complete(attempt)

        _assert_no_secret_or_symlink(_run_logs_dir(attempt), _CLAUDE_SECRET)
        preserved = _preserved(attempt, "claude-projects")
        assert sorted(str(p.relative_to(preserved)) for p in preserved.rglob("*")) == [
            _CLAUDE_PROJECT,
            f"{_CLAUDE_PROJECT}/{_CLAUDE_SESSION}",
            f"{_CLAUDE_PROJECT}/{_CLAUDE_SESSION}.jsonl",
            f"{_CLAUDE_PROJECT}/{_CLAUDE_SESSION}/subagents",
            f"{_CLAUDE_PROJECT}/{_CLAUDE_SESSION}/subagents/agent-x.jsonl",
            f"{_CLAUDE_PROJECT}/{_CLAUDE_SESSION}/subagents/agent-x.meta.json",
        ]

    def test_symlinked_session_root_is_refused(self, tmp_path: Path) -> None:
        attempt = _lease_attempt(tmp_path, "claude-code-cli", _CLAUDE_BLOB)
        slot = attempt.lease.slot_dir
        _write(slot / "elsewhere" / _CLAUDE_PROJECT / "secret.jsonl", f'"{_CLAUDE_SECRET}"\n')
        (slot / "projects").symlink_to(slot / "elsewhere", target_is_directory=True)

        _complete(attempt)

        assert not attempt.lease.slot_dir.exists()
        assert not _preserved(attempt, "claude-projects").exists()
        _assert_no_secret_or_symlink(_run_logs_dir(attempt), _CLAUDE_SECRET)

    def test_file_replaced_after_planning_aborts_the_whole_set(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        attempt = _lease_attempt(tmp_path, "codex-cli", _CODEX_BLOB)
        _write_codex_rollouts(attempt.lease.slot_dir)
        rollout = (
            attempt.lease.slot_dir
            / ".codex"
            / "sessions"
            / "2026"
            / "09"
            / "14"
            / f"{_ROLLOUT}.jsonl"
        )
        credential = attempt.lease.slot_dir / ".codex" / "auth.json"
        original_copy = slot_coordinator_module._copy_native_session_log_files

        def replace_then_copy(root_fd: int, planned: list[Path], staging: Path) -> None:
            rollout.unlink()
            rollout.symlink_to(credential)
            original_copy(root_fd, planned, staging)

        monkeypatch.setattr(
            slot_coordinator_module,
            "_copy_native_session_log_files",
            replace_then_copy,
        )
        with caplog.at_level(logging.WARNING, logger="metaproc.dispatch.slot_coordinator"):
            _complete(attempt)

        assert "failed to preserve native session log set" in caplog.text
        assert not _preserved(attempt, "codex-sessions").exists()
        _assert_no_secret_or_symlink(_run_logs_dir(attempt), _CODEX_SECRET)


def _assert_no_secret_or_symlink(logs_dir: Path, secret: str) -> None:
    for dirpath, dirnames, filenames in os.walk(logs_dir):
        for name in [*dirnames, *filenames]:
            path = Path(dirpath) / name
            assert not path.is_symlink(), f"symlink copied into .logs/: {path}"
        for name in filenames:
            path = Path(dirpath) / name
            assert secret.encode() not in path.read_bytes(), f"credential leaked into {path}"


class TestPreservationIsBestEffort:
    def test_copy_failure_is_logged_and_teardown_still_runs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        attempt = _lease_attempt(tmp_path, "codex-cli", _CODEX_BLOB)
        _write_codex_rollouts(attempt.lease.slot_dir)

        def fail_copy(*_args: object, **_kwargs: object) -> None:
            raise PermissionError("synthetic copy failure")

        monkeypatch.setattr("metaproc.dispatch.slot_coordinator.shutil.copyfileobj", fail_copy)
        with caplog.at_level(logging.WARNING, logger="metaproc.dispatch.slot_coordinator"):
            _complete(attempt)

        assert not attempt.lease.slot_dir.exists()
        assert attempt.lease.label_lock_path is not None
        assert not attempt.lease.label_lock_path.exists()
        assert "native session log" in caplog.text
        assert not _preserved(attempt, "codex-sessions").exists()
        native_parent = _run_logs_dir(attempt) / "native" / "predict" / "AAPL"
        assert _hidden_entries(native_parent) == []

    def test_publish_failure_leaves_no_partial_tree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        attempt = _lease_attempt(tmp_path, "claude-code-cli", _CLAUDE_BLOB)
        _write_claude_transcripts(attempt.lease.slot_dir)

        def fail_rename(*_args: object, **_kwargs: object) -> None:
            raise OSError("synthetic rename failure")

        monkeypatch.setattr("metaproc.dispatch.slot_coordinator.os.rename", fail_rename)
        with caplog.at_level(logging.WARNING, logger="metaproc.dispatch.slot_coordinator"):
            _complete(attempt)

        assert not attempt.lease.slot_dir.exists()
        assert "native session log" in caplog.text
        assert not _preserved(attempt, "claude-projects").exists()
        native_parent = _run_logs_dir(attempt) / "native" / "predict" / "AAPL"
        assert _hidden_entries(native_parent) == []

    def test_existing_destination_is_not_replaced(self, tmp_path: Path) -> None:
        attempt = _lease_attempt(tmp_path, "codex-cli", _CODEX_BLOB)
        _write_codex_rollouts(attempt.lease.slot_dir)
        preserved = _preserved(attempt, "codex-sessions")
        _write(preserved / "earlier.jsonl", "earlier\n")

        _complete(attempt)

        assert not attempt.lease.slot_dir.exists()
        assert [p.name for p in preserved.iterdir()] == ["earlier.jsonl"]
        assert _hidden_entries(attempt.session_log.parent) == []

    def test_existing_empty_destination_is_not_replaced(self, tmp_path: Path) -> None:
        attempt = _lease_attempt(tmp_path, "codex-cli", _CODEX_BLOB)
        _write_codex_rollouts(attempt.lease.slot_dir)
        preserved = _preserved(attempt, "codex-sessions")
        preserved.mkdir(parents=True)

        _complete(attempt)

        assert preserved.is_dir()
        assert list(preserved.iterdir()) == []
        assert _hidden_entries(preserved.parent) == []

    def test_no_session_files_writes_nothing(self, tmp_path: Path) -> None:
        # Claude's default `--no-session-persistence` leaves no `projects/` tree.
        attempt = _lease_attempt(tmp_path, "claude-code-cli", _CLAUDE_BLOB)

        _complete(attempt)

        assert not attempt.lease.slot_dir.exists()
        assert sorted(p.name for p in attempt.session_log.parent.iterdir()) == [
            f"{_SESSION_STEM}.jsonl"
        ]
        assert not (_run_logs_dir(attempt) / "native").exists()


def _hidden_entries(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir() if p.name.startswith("."))
