"""Tests for ``metaproc auth setup`` — the one-step Vehicle A onboarding command.

Exercises three input modes (interactive subprocess spawn, piped stdin,
``--token-file``), backend resolution (local / gcp-secret-manager /
both with graceful degradation), the never-print-payload invariant on
the new path, and the token-handling discipline (env-var scoped only
during inner push, restored on exit).

Spec: docs/project/specs/active/plan-2026-04-28-claude-code-auth-vehicle-a-pool-redesign.md.
"""

from __future__ import annotations

import os as _os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import metaproc.commands.auth as auth_mod
from metaproc.cli import app
from metaproc.dispatch.credential_pool import (
    ConcurrentModificationError,
    EntryState,
    PoolEntry,
    Vehicle,
)

_OAUTH_TOKEN = "sk-ant-oat01-AUTH-SETUP-DO-NOT-PRINT"
_TOKEN_MARKER = "AUTH-SETUP-DO-NOT-PRINT"


# ── Multi-backend fake ──────────────────────────────────────────


@dataclass
class _InMemoryBackend:
    name: str = "anonymous"
    entries: dict[tuple[str, str], PoolEntry] = field(default_factory=dict)
    counter: int = 0

    def get_entry(self, adapter: str, label: str) -> PoolEntry:
        key = (adapter, label)
        if key not in self.entries:
            msg = f"no pool entry for {adapter}/{label}"
            raise KeyError(msg)
        return self.entries[key]

    def list_entries(self, adapter: str | None = None) -> list[PoolEntry]:
        return [
            entry for (a, _label), entry in self.entries.items() if adapter is None or a == adapter
        ]

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
            msg = f"etag mismatch: {expected_etag} != {existing.etag}"
            raise ConcurrentModificationError(msg)
        self.counter += 1
        new_blob = blob if blob is not None else (existing.blob if existing else "")
        new_state = (
            state
            if state is not None
            else (existing.state if existing else EntryState(status="active", fp=""))
        )
        new_etag = f"{self.name}-etag-{self.counter}"
        self.entries[key] = PoolEntry(
            adapter=adapter,
            label=label,
            blob=new_blob,
            state=new_state,
            etag=new_etag,
        )
        return new_etag

    def delete_entry(self, adapter: str, label: str) -> None:
        self.entries.pop((adapter, label), None)


@pytest.fixture
def both_pools(monkeypatch: pytest.MonkeyPatch) -> dict[str, _InMemoryBackend]:
    """Install per-backend in-memory pools so we can verify dual-push semantics.

    `auth setup --backend both` should hit BOTH local and
    gcp-secret-manager. The factory returns a different pool per
    backend name so the test can inspect each independently.
    """
    pools = {
        "local": _InMemoryBackend(name="local"),
        "gcp-secret-manager": _InMemoryBackend(name="gcp"),
    }

    def factory(backend_name: str, *, pool_user: str) -> _InMemoryBackend:  # noqa: ARG001
        return pools[backend_name]

    monkeypatch.setattr(auth_mod, "BACKEND_FACTORY", factory)
    # METAPROC_GCP_PROJECT must be set for `--backend both` to include
    # gcp-secret-manager.
    monkeypatch.setenv("METAPROC_GCP_PROJECT", "test-project")
    return pools


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _assert_no_token_leak(stdout: str, stderr: str) -> None:
    assert _TOKEN_MARKER not in stdout, "token leaked to stdout"
    assert _TOKEN_MARKER not in stderr, "token leaked to stderr"


# ── --token-file mode ────────────────────────────────────────────


class TestSetupFromTokenFile:
    def test_token_file_pushes_to_both_backends(
        self,
        runner: CliRunner,
        both_pools: dict[str, _InMemoryBackend],
        tmp_path: Path,
    ) -> None:
        token_file = tmp_path / "token.txt"
        token_file.write_text(_OAUTH_TOKEN + "\n")
        # --no-probe so we don't call out to the real claude binary.
        result = runner.invoke(
            app,
            [
                "auth",
                "setup",
                "alt1",
                "--token-file",
                str(token_file),
                "--no-probe",
            ],
        )
        assert result.exit_code == 0, result.stderr
        _assert_no_token_leak(result.stdout, result.stderr)
        # Both backends got the entry, with V-A vehicle.
        for be_name, pool in both_pools.items():
            assert ("claude-code-cli", "alt1") in pool.entries, f"missing in {be_name}"
            entry = pool.entries[("claude-code-cli", "alt1")]
            assert entry.state.vehicle == Vehicle.OAUTH_TOKEN
            # Token whitespace-stripped, stored as-is in the blob.
            assert entry.blob == _OAUTH_TOKEN
            assert entry.state.status == "active"

    def test_token_file_with_local_only(
        self,
        runner: CliRunner,
        both_pools: dict[str, _InMemoryBackend],
        tmp_path: Path,
    ) -> None:
        token_file = tmp_path / "token.txt"
        token_file.write_text(_OAUTH_TOKEN)
        result = runner.invoke(
            app,
            [
                "auth",
                "setup",
                "alt1",
                "--token-file",
                str(token_file),
                "--backend",
                "local",
                "--no-probe",
            ],
        )
        assert result.exit_code == 0, result.stderr
        # Only local got the entry.
        assert ("claude-code-cli", "alt1") in both_pools["local"].entries
        assert ("claude-code-cli", "alt1") not in both_pools["gcp-secret-manager"].entries

    def test_empty_token_file_rejected(
        self,
        runner: CliRunner,
        both_pools: dict[str, _InMemoryBackend],  # noqa: ARG002
        tmp_path: Path,
    ) -> None:
        token_file = tmp_path / "empty.txt"
        token_file.write_text("")
        result = runner.invoke(
            app,
            ["auth", "setup", "alt1", "--token-file", str(token_file), "--no-probe"],
        )
        assert result.exit_code != 0
        combined = result.stderr + result.stdout
        assert "empty" in combined


# ── piped stdin mode ────────────────────────────────────────────


class TestSetupFromStdin:
    def test_piped_stdin_pushes_to_both(
        self,
        runner: CliRunner,
        both_pools: dict[str, _InMemoryBackend],
    ) -> None:
        # CliRunner.invoke(input=...) makes stdin a non-TTY. The setup
        # command's _source_setup_token will read from there.
        result = runner.invoke(
            app,
            ["auth", "setup", "alt1", "--no-probe"],
            input=_OAUTH_TOKEN + "\n",
        )
        assert result.exit_code == 0, result.stderr
        _assert_no_token_leak(result.stdout, result.stderr)
        for pool in both_pools.values():
            entry = pool.entries[("claude-code-cli", "alt1")]
            assert entry.blob == _OAUTH_TOKEN
            assert entry.state.vehicle == Vehicle.OAUTH_TOKEN

    def test_empty_stdin_rejected(
        self,
        runner: CliRunner,
        both_pools: dict[str, _InMemoryBackend],  # noqa: ARG002
    ) -> None:
        result = runner.invoke(app, ["auth", "setup", "alt1", "--no-probe"], input="")
        assert result.exit_code != 0
        combined = result.stderr + result.stdout
        assert "empty" in combined


# ── interactive subprocess-spawn mode ────────────────────────────


class TestSetupInteractiveSpawn:
    def test_interactive_spawns_claude_setup_token_and_uses_stdout(
        self,
        runner: CliRunner,
        both_pools: dict[str, _InMemoryBackend],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Force isatty so the interactive code path is taken. CliRunner
        # normally mocks stdin to a non-TTY; monkeypatch sys.stdin.isatty.
        monkeypatch.setattr(auth_mod, "_stdin_is_interactive", lambda: True)

        # Mock the subprocess spawn to return a fake token.
        @dataclass
        class _FakeResult:
            returncode: int
            stdout: str
            stderr: str = ""

        def _fake_run(cmd: list[str], **_kwargs: Any) -> _FakeResult:
            assert cmd == ["claude", "setup-token"]
            return _FakeResult(returncode=0, stdout=_OAUTH_TOKEN)

        monkeypatch.setattr("subprocess.run", _fake_run)

        result = runner.invoke(app, ["auth", "setup", "alt1", "--no-probe"])
        assert result.exit_code == 0, result.stderr
        _assert_no_token_leak(result.stdout, result.stderr)
        for pool in both_pools.values():
            entry = pool.entries[("claude-code-cli", "alt1")]
            assert entry.blob == _OAUTH_TOKEN

    def test_claude_binary_missing_fails_actionably(
        self,
        runner: CliRunner,
        both_pools: dict[str, _InMemoryBackend],  # noqa: ARG002
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(auth_mod, "_stdin_is_interactive", lambda: True)

        def _missing(*_args: Any, **_kwargs: Any) -> None:
            raise FileNotFoundError("claude not on PATH")

        monkeypatch.setattr("subprocess.run", _missing)

        result = runner.invoke(app, ["auth", "setup", "alt1"])
        assert result.exit_code != 0
        combined = result.stderr + result.stdout
        assert "claude" in combined.lower()
        assert "PATH" in combined

    def test_setup_token_nonzero_exit_fails(
        self,
        runner: CliRunner,
        both_pools: dict[str, _InMemoryBackend],  # noqa: ARG002
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(auth_mod, "_stdin_is_interactive", lambda: True)

        @dataclass
        class _FakeResult:
            returncode: int
            stdout: str = ""
            stderr: str = ""

        def _fake_run(*_args: Any, **_kwargs: Any) -> _FakeResult:
            return _FakeResult(returncode=1)

        monkeypatch.setattr("subprocess.run", _fake_run)

        result = runner.invoke(app, ["auth", "setup", "alt1"])
        assert result.exit_code != 0
        combined = result.stderr + result.stdout
        assert "setup-token" in combined
        assert "failed" in combined.lower()

    def test_extracts_token_from_tui_banner_output(
        self,
        runner: CliRunner,
        both_pools: dict[str, _InMemoryBackend],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Regression for the v2.1.119 bug: `claude setup-token` writes
        # an ASCII banner + ANSI escape codes + congratulatory text +
        # the token + storage instructions. Capturing the raw stdout
        # and treating it as the token sends garbage to the pool and
        # blows up at the first probe with "Header has invalid value:
        # 'Bearer <ansi> Welcome to Claude Code...'".
        # The extractor must pull just the sk-ant-oat01-... line out.
        monkeypatch.setattr(auth_mod, "_stdin_is_interactive", lambda: True)

        tui_stdout = (
            "\x1b[?2004h\x1b[?1004h\x1b[?2031hWelcome to Claude Code v2.1.119\n"
            "" + "…" * 50 + "\n"
            "          ░░░░░░          \n"
            "      ASCII art lines      \n"
            "\x1b[32m✓ Long-lived authentication token created successfully!\x1b[0m\n"
            "Your OAuth token (valid for 1 year):\n"
            "\n"
            f"{_OAUTH_TOKEN}\n"
            "\n"
            "Store this token securely. You won't be able to see it again.\n"
            "Use this token by setting: export CLAUDE_CODE_OAUTH_TOKEN=<token>\n"
        )

        @dataclass
        class _FakeResult:
            returncode: int = 0
            stdout: str = ""
            stderr: str = ""

        def _fake_run(*_args: Any, **_kwargs: Any) -> _FakeResult:
            return _FakeResult(stdout=tui_stdout)

        monkeypatch.setattr("subprocess.run", _fake_run)

        result = runner.invoke(app, ["auth", "setup", "alt1", "--no-probe"])
        assert result.exit_code == 0, result.stderr
        # Token reaches the pool clean — no banner, no ANSI codes.
        for pool in both_pools.values():
            entry = pool.entries[("claude-code-cli", "alt1")]
            assert entry.blob == _OAUTH_TOKEN, f"expected clean token in pool, got {entry.blob!r}"
        # And nothing in the captured tui_stdout reaches the
        # operator's terminal.
        _assert_no_token_leak(result.stdout, result.stderr)
        assert "Welcome to Claude Code" not in result.stdout

    def test_no_token_in_mint_output_fails_actionably(
        self,
        runner: CliRunner,
        both_pools: dict[str, _InMemoryBackend],  # noqa: ARG002
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # If `claude setup-token` ever changes its output format and
        # stops emitting a sk-ant-oat01- line, the operator gets a
        # clear actionable error pointing at the file/stdin
        # alternatives — not a silent garbage-push.
        monkeypatch.setattr(auth_mod, "_stdin_is_interactive", lambda: True)

        @dataclass
        class _FakeResult:
            returncode: int = 0
            stdout: str = "Welcome to Claude Code v3.0.0\nSome new TUI flow\n"
            stderr: str = ""

        def _fake_run(*_args: Any, **_kwargs: Any) -> _FakeResult:
            return _FakeResult()

        monkeypatch.setattr("subprocess.run", _fake_run)

        result = runner.invoke(app, ["auth", "setup", "alt1", "--no-probe"])
        assert result.exit_code != 0
        combined = result.stderr + result.stdout
        assert "did not emit a recognizable OAuth token" in combined
        assert "--token-file" in combined


# ── adapter gating ───────────────────────────────────────────────


class TestSetupAdapterGating:
    def test_non_claude_adapter_rejected_with_actionable_message(
        self,
        runner: CliRunner,
        both_pools: dict[str, _InMemoryBackend],  # noqa: ARG002
        tmp_path: Path,
    ) -> None:
        token_file = tmp_path / "tok.txt"
        token_file.write_text(_OAUTH_TOKEN)
        result = runner.invoke(
            app,
            [
                "auth",
                "setup",
                "alt1",
                "--adapter",
                "codex-cli",
                "--token-file",
                str(token_file),
            ],
        )
        assert result.exit_code != 0
        combined = result.stderr + result.stdout
        # Operator should learn what to do for non-Claude adapters.
        assert "claude-code-cli" in combined
        assert "auth push" in combined
        assert "login-credentials" in combined


# ── --backend graceful degradation ───────────────────────────────


class TestSetupBackendDegradation:
    def test_both_with_no_gcp_project_pushes_local_only(
        self,
        runner: CliRunner,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Per the spec: 'both' silently degrades to local-only when
        # METAPROC_GCP_PROJECT is unset (gcp_backend would error).
        monkeypatch.delenv("METAPROC_GCP_PROJECT", raising=False)
        local_pool = _InMemoryBackend(name="local")
        gcp_pool = _InMemoryBackend(name="gcp")
        pools = {"local": local_pool, "gcp-secret-manager": gcp_pool}

        def factory(backend_name: str, *, pool_user: str) -> _InMemoryBackend:  # noqa: ARG001
            return pools[backend_name]

        monkeypatch.setattr(auth_mod, "BACKEND_FACTORY", factory)
        token_file = tmp_path / "tok.txt"
        token_file.write_text(_OAUTH_TOKEN)
        result = runner.invoke(
            app,
            [
                "auth",
                "setup",
                "alt1",
                "--backend",
                "both",
                "--token-file",
                str(token_file),
                "--no-probe",
            ],
        )
        assert result.exit_code == 0, result.stderr
        # Local pushed; gcp NOT pushed.
        assert ("claude-code-cli", "alt1") in local_pool.entries
        assert ("claude-code-cli", "alt1") not in gcp_pool.entries

    def test_invalid_backend_value_rejected(
        self,
        runner: CliRunner,
        both_pools: dict[str, _InMemoryBackend],  # noqa: ARG002
        tmp_path: Path,
    ) -> None:
        token_file = tmp_path / "tok.txt"
        token_file.write_text(_OAUTH_TOKEN)
        result = runner.invoke(
            app,
            [
                "auth",
                "setup",
                "alt1",
                "--backend",
                "rolodex",  # not a real backend
                "--token-file",
                str(token_file),
            ],
        )
        assert result.exit_code != 0
        combined = result.stderr + result.stdout
        assert "rolodex" in combined or "backend" in combined.lower()


# ── token-handling discipline ────────────────────────────────────


class TestSetupTokenHandling:
    def test_env_var_restored_after_setup(
        self,
        runner: CliRunner,
        both_pools: dict[str, _InMemoryBackend],  # noqa: ARG002
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Pre-existing CLAUDE_CODE_OAUTH_TOKEN must be restored after
        # the setup command finishes (we set it transiently for the
        # inner push_cmd's env-var fallback).
        original = "PRE-EXISTING-TOKEN"
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", original)
        token_file = tmp_path / "tok.txt"
        token_file.write_text(_OAUTH_TOKEN)
        result = runner.invoke(
            app,
            ["auth", "setup", "alt1", "--token-file", str(token_file), "--no-probe"],
        )
        assert result.exit_code == 0, result.stderr

        assert _os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") == original

    def test_env_var_unset_when_no_pre_existing(
        self,
        runner: CliRunner,
        both_pools: dict[str, _InMemoryBackend],  # noqa: ARG002
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        token_file = tmp_path / "tok.txt"
        token_file.write_text(_OAUTH_TOKEN)
        result = runner.invoke(
            app,
            ["auth", "setup", "alt1", "--token-file", str(token_file), "--no-probe"],
        )
        assert result.exit_code == 0, result.stderr

        assert "CLAUDE_CODE_OAUTH_TOKEN" not in _os.environ
