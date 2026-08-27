"""Tests for the Vehicle A operator surface on ``metaproc auth``.

Spec: src/metaproc/docs/metaproc-design.md
Regression coverage (Phase 2 of the original design).

Exercises the new flags on ``auth push`` (``--vehicle``, ``--token-file``,
``--account-id``, ``--organization-uuid``, ``--quota-group``) plus the
vehicle-aware fields surfaced through ``auth probe``, ``auth list``, and
``auth status``. Reuses the in-memory pool backend pattern from
``test_auth_command.py``.

Token sourcing precedence under ``--vehicle oauth-token``:
1. ``--token-file <path>`` — file contents stripped of whitespace.
2. Stdin (when not a TTY).
3. ``$CLAUDE_CODE_OAUTH_TOKEN`` env var.

Vehicle A push must NOT call ``capture_credential`` (Keychain is the
Vehicle B path). Vehicle B push (default) preserves the existing
behavior with ``vehicle=login_credentials`` written into EntryState.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

import metaproc.commands.auth as auth_mod
from metaproc.adapters.claude_code import ClaudeCodeCliAdapter
from metaproc.cli import app
from metaproc.dispatch.credential_pool import (
    ConcurrentModificationError,
    EntryState,
    PoolEntry,
    QuotaGroup,
    Vehicle,
)

_OAUTH_TOKEN = "sk-ant-oat01-VEHICLE-A-DO-NOT-PRINT"
_OAUTH_MARKER = "VEHICLE-A-DO-NOT-PRINT"
_LOGIN_BLOB = json.dumps({"claudeAiOauth": {"accessToken": "sk-ant-LOGIN-DO-NOT-PRINT"}})
_LOGIN_MARKER = "LOGIN-DO-NOT-PRINT"


# ── In-memory backend fake ──────────────────────────────────────


@dataclass
class _InMemoryBackend:
    entries: dict[tuple[str, str], PoolEntry] = field(default_factory=dict)
    etag_counter: int = 0

    def _next_etag(self) -> str:
        self.etag_counter += 1
        return f"etag-{self.etag_counter}"

    def get_entry(self, adapter: str, label: str) -> PoolEntry:
        key = (adapter, label)
        if key not in self.entries:
            msg = f"no pool entry for {adapter}/{label}"
            raise KeyError(msg)
        return self.entries[key]

    def list_entries(self, adapter: str | None = None) -> list[PoolEntry]:
        out: list[PoolEntry] = []
        for (a, _label), entry in self.entries.items():
            if adapter is None or a == adapter:
                out.append(entry)
        return out

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
        new_blob = blob if blob is not None else (existing.blob if existing else "")
        new_state = (
            state
            if state is not None
            else (existing.state if existing else EntryState(status="active", fp=""))
        )
        new_etag = self._next_etag()
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


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def fake_pool(monkeypatch: pytest.MonkeyPatch) -> _InMemoryBackend:
    pool = _InMemoryBackend()

    def factory(_backend: str, *, pool_user: str) -> _InMemoryBackend:  # noqa: ARG001
        return pool

    monkeypatch.setattr(auth_mod, "BACKEND_FACTORY", factory)
    return pool


@pytest.fixture
def keychain_blob(monkeypatch: pytest.MonkeyPatch) -> None:
    """Short-circuit ClaudeCodeCliAdapter.capture_credential for the Vehicle B path."""

    monkeypatch.setattr(ClaudeCodeCliAdapter, "capture_credential", lambda self: _LOGIN_BLOB)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _assert_no_secret(stdout: str, stderr: str) -> None:
    """Never-print-payload invariant for both vehicles."""
    assert _OAUTH_MARKER not in stdout, "Vehicle A token leaked to stdout"
    assert _OAUTH_MARKER not in stderr, "Vehicle A token leaked to stderr"
    assert _LOGIN_MARKER not in stdout, "Vehicle B blob leaked to stdout"
    assert _LOGIN_MARKER not in stderr, "Vehicle B blob leaked to stderr"
    assert "accessToken" not in stdout, "raw OAuth field name leaked to stdout"


# ── push --vehicle oauth-token (Vehicle A) ───────────────────────


class TestPushVehicleA:
    def test_push_oauth_token_via_file_creates_active_vehicle_a_entry(
        self, runner: CliRunner, fake_pool: _InMemoryBackend, tmp_path: Path
    ) -> None:
        token_file = tmp_path / "token.txt"
        # Trailing newline simulates `claude setup-token > token.txt`.
        token_file.write_text(_OAUTH_TOKEN + "\n")
        result = runner.invoke(
            app,
            [
                "auth",
                "push",
                "--adapter",
                "claude-code-cli",
                "--label",
                "alt1",
                "--vehicle",
                "oauth-token",
                "--token-file",
                str(token_file),
            ],
        )
        assert result.exit_code == 0, result.stderr
        _assert_no_secret(result.stdout, result.stderr)
        entry = fake_pool.entries[("claude-code-cli", "alt1")]
        assert entry.state.vehicle == Vehicle.OAUTH_TOKEN
        # Whitespace-stripped — the trailing newline must not survive.
        assert entry.blob == _OAUTH_TOKEN
        assert entry.state.status == "active"

    def test_push_oauth_token_rejects_missing_token_source(
        self, runner: CliRunner, fake_pool: _InMemoryBackend, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force every source to be empty: no --token-file, stdin is a TTY,
        # and CLAUDE_CODE_OAUTH_TOKEN is unset.
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        result = runner.invoke(
            app,
            [
                "auth",
                "push",
                "--adapter",
                "claude-code-cli",
                "--label",
                "alt1",
                "--vehicle",
                "oauth-token",
            ],
        )
        # No token source → typer.BadParameter → exit code 2 (CLI usage error).
        assert result.exit_code != 0
        assert ("token source" in result.stderr) or ("token source" in result.stdout)

    def test_push_oauth_token_via_env(
        self,
        runner: CliRunner,
        fake_pool: _InMemoryBackend,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", _OAUTH_TOKEN)
        result = runner.invoke(
            app,
            [
                "auth",
                "push",
                "--adapter",
                "claude-code-cli",
                "--label",
                "alt2",
                "--vehicle",
                "oauth-token",
            ],
        )
        assert result.exit_code == 0, result.stderr
        _assert_no_secret(result.stdout, result.stderr)
        entry = fake_pool.entries[("claude-code-cli", "alt2")]
        assert entry.state.vehicle == Vehicle.OAUTH_TOKEN
        assert entry.blob == _OAUTH_TOKEN

    def test_push_oauth_token_does_not_call_capture_credential(
        self,
        runner: CliRunner,
        fake_pool: _InMemoryBackend,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:

        def boom(self: ClaudeCodeCliAdapter) -> str:  # noqa: ARG001
            msg = "capture_credential MUST NOT be called for Vehicle A push"
            raise AssertionError(msg)

        monkeypatch.setattr(ClaudeCodeCliAdapter, "capture_credential", boom)
        token_file = tmp_path / "token.txt"
        token_file.write_text(_OAUTH_TOKEN)
        result = runner.invoke(
            app,
            [
                "auth",
                "push",
                "--adapter",
                "claude-code-cli",
                "--label",
                "alt1",
                "--vehicle",
                "oauth-token",
                "--token-file",
                str(token_file),
            ],
        )
        assert result.exit_code == 0, result.stderr

    def test_push_oauth_token_with_org_quota_group_auto(
        self, runner: CliRunner, fake_pool: _InMemoryBackend, tmp_path: Path
    ) -> None:
        token_file = tmp_path / "token.txt"
        token_file.write_text(_OAUTH_TOKEN)
        result = runner.invoke(
            app,
            [
                "auth",
                "push",
                "--adapter",
                "claude-code-cli",
                "--label",
                "alt1",
                "--vehicle",
                "oauth-token",
                "--token-file",
                str(token_file),
                "--account-id",
                "abc1234567890def",
                "--organization-uuid",
                "abc12345-6789-4abc-9def-0123456789ab",
                # --quota-group defaults to "auto"; org_uuid wins.
            ],
        )
        assert result.exit_code == 0, result.stderr
        entry = fake_pool.entries[("claude-code-cli", "alt1")]
        assert entry.state.account_id == "abc1234567890def"
        assert entry.state.organization_uuid == "abc12345-6789-4abc-9def-0123456789ab"
        assert entry.state.quota_group == QuotaGroup(
            kind="org", value="abc12345-6789-4abc-9def-0123456789ab"
        )

    def test_push_oauth_token_explicit_account_quota_group(
        self, runner: CliRunner, fake_pool: _InMemoryBackend, tmp_path: Path
    ) -> None:
        token_file = tmp_path / "token.txt"
        token_file.write_text(_OAUTH_TOKEN)
        result = runner.invoke(
            app,
            [
                "auth",
                "push",
                "--adapter",
                "claude-code-cli",
                "--label",
                "alt1",
                "--vehicle",
                "oauth-token",
                "--token-file",
                str(token_file),
                "--quota-group",
                "account:def67890abcd1234",
            ],
        )
        assert result.exit_code == 0, result.stderr
        entry = fake_pool.entries[("claude-code-cli", "alt1")]
        assert entry.state.quota_group == QuotaGroup(kind="account", value="def67890abcd1234")

    def test_push_rejects_invalid_quota_group_form(
        self, runner: CliRunner, fake_pool: _InMemoryBackend, tmp_path: Path
    ) -> None:
        token_file = tmp_path / "token.txt"
        token_file.write_text(_OAUTH_TOKEN)
        result = runner.invoke(
            app,
            [
                "auth",
                "push",
                "--adapter",
                "claude-code-cli",
                "--label",
                "alt1",
                "--vehicle",
                "oauth-token",
                "--token-file",
                str(token_file),
                "--quota-group",
                "garbage:form",
            ],
        )
        assert result.exit_code != 0
        # Either "kind must be" or "expected" (depending on parse path).
        combined = result.stderr + result.stdout
        assert "kind must be" in combined or "expected" in combined

    def test_push_rejects_invalid_vehicle_value(
        self, runner: CliRunner, fake_pool: _InMemoryBackend, tmp_path: Path
    ) -> None:
        result = runner.invoke(
            app,
            [
                "auth",
                "push",
                "--adapter",
                "claude-code-cli",
                "--label",
                "alt1",
                "--vehicle",
                "oauth-tokenz",  # typo
            ],
        )
        assert result.exit_code != 0
        combined = result.stderr + result.stdout
        assert "oauth-token" in combined or "vehicle" in combined.lower()


# ── push --vehicle login-credentials (Vehicle B) ─────────────────


class TestPushVehicleB:
    def test_default_push_for_claude_is_vehicle_a(
        self,
        runner: CliRunner,
        fake_pool: _InMemoryBackend,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Vehicle A redesign: bare `auth push --adapter claude-code-cli`
        # (no --vehicle flag) defaults to oauth-token. With no token
        # source supplied, the push fails-fast with a typer.BadParameter
        # asking for --token-file / stdin / env var. Operators who
        # actually want Vehicle B opt in explicitly with
        # `--vehicle login-credentials`.
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        result = runner.invoke(
            app,
            ["auth", "push", "--adapter", "claude-code-cli", "--label", "laptop"],
        )
        # No token source under the new V-A default → exit code != 0.
        assert result.exit_code != 0
        combined = result.stderr + result.stdout
        assert "token source" in combined

    def test_explicit_login_credentials_flag_calls_capture_credential(
        self,
        runner: CliRunner,
        fake_pool: _InMemoryBackend,
        keychain_blob: None,  # noqa: ARG002
    ) -> None:
        result = runner.invoke(
            app,
            [
                "auth",
                "push",
                "--adapter",
                "claude-code-cli",
                "--label",
                "laptop",
                "--vehicle",
                "login-credentials",
            ],
        )
        assert result.exit_code == 0, result.stderr
        entry = fake_pool.entries[("claude-code-cli", "laptop")]
        assert entry.state.vehicle == Vehicle.LOGIN_CREDENTIALS


# ── list / status surface the vehicle column ─────────────────────


class TestListAndStatusRenderVehicle:
    def _seed_two_vehicle_entries(self, fake_pool: _InMemoryBackend) -> None:
        fake_pool.entries[("claude-code-cli", "alt1")] = PoolEntry(
            adapter="claude-code-cli",
            label="alt1",
            blob=_OAUTH_TOKEN,
            state=EntryState(
                status="active",
                fp="fp-a",
                vehicle=Vehicle.OAUTH_TOKEN,
                account_id="abc1234567890def",
                organization_uuid="abc12345-6789-4abc-9def-0123456789ab",
                quota_group=QuotaGroup(kind="org", value="abc12345-6789-4abc-9def-0123456789ab"),
            ),
            etag="e1",
        )
        fake_pool.entries[("claude-code-cli", "laptop")] = PoolEntry(
            adapter="claude-code-cli",
            label="laptop",
            blob=_LOGIN_BLOB,
            state=EntryState(status="active", fp="fp-b"),
            etag="e2",
        )

    def test_list_renders_vehicle_per_entry(
        self, runner: CliRunner, fake_pool: _InMemoryBackend
    ) -> None:
        self._seed_two_vehicle_entries(fake_pool)
        result = runner.invoke(app, ["auth", "list", "--json"])
        assert result.exit_code == 0, result.stderr
        _assert_no_secret(result.stdout, result.stderr)
        # JSON is wrapped in a string by the OutputManager; the
        # rendered shape includes both vehicle values.
        assert "claude_code_oauth_token" in result.stdout
        assert "login_credentials" in result.stdout

    def test_status_text_tags_vehicle_a_with_A(
        self, runner: CliRunner, fake_pool: _InMemoryBackend
    ) -> None:
        self._seed_two_vehicle_entries(fake_pool)
        result = runner.invoke(app, ["auth", "status", "--no-color"])
        assert result.exit_code == 0, result.stderr
        _assert_no_secret(result.stdout, result.stderr)
        # Vehicle A and Vehicle B labels are both present, tagged.
        # Format: "  alt1         [A] ACTIVE ..."
        assert "[A]" in result.stdout
        assert "[B]" in result.stdout

    def test_status_json_includes_new_fields(
        self, runner: CliRunner, fake_pool: _InMemoryBackend
    ) -> None:
        self._seed_two_vehicle_entries(fake_pool)
        result = runner.invoke(app, ["auth", "status", "--json"])
        assert result.exit_code == 0, result.stderr
        # The JSON shape is wrapped — strip and parse.
        text = result.stdout.strip()
        # Find the JSON dict; OutputManager may wrap, but a plain
        # `--json` write should be valid JSON at the top.
        parsed = json.loads(text)
        labels = parsed["adapters"][0]["labels"]
        # alt1 (Vehicle A) and laptop (Vehicle B), sorted by status then label.
        vehicles = {lr["label"]: lr["vehicle"] for lr in labels}
        assert vehicles["alt1"] == "claude_code_oauth_token"
        assert vehicles["laptop"] == "login_credentials"
        # Quota group fields surfaced.
        alt1_record = next(lr for lr in labels if lr["label"] == "alt1")
        assert alt1_record["quota_group_kind"] == "org"
        assert alt1_record["organization_uuid"] == ("abc12345-6789-4abc-9def-0123456789ab")


# ── _resolve_quota_group helper unit tests ───────────────────────


class TestResolveQuotaGroup:
    def test_auto_prefers_organization_uuid(self) -> None:
        qg = auth_mod._resolve_quota_group(
            "auto",
            organization_uuid="abc12345-6789-4abc-9def-0123456789ab",
            account_id="abc1234567890def",
        )
        assert qg == QuotaGroup(kind="org", value="abc12345-6789-4abc-9def-0123456789ab")

    def test_auto_falls_back_to_account_id(self) -> None:
        qg = auth_mod._resolve_quota_group(
            "auto", organization_uuid=None, account_id="abc1234567890def"
        )
        assert qg == QuotaGroup(kind="account", value="abc1234567890def")

    def test_auto_unknown_when_neither_supplied(self) -> None:
        qg = auth_mod._resolve_quota_group("auto", organization_uuid=None, account_id=None)
        assert qg == QuotaGroup.unknown()

    def test_explicit_unknown(self) -> None:
        qg = auth_mod._resolve_quota_group(
            "unknown",
            organization_uuid="abc12345-6789-4abc-9def-0123456789ab",
            account_id="abc1234567890def",
        )
        # Explicit unknown wins over derivable values.
        assert qg == QuotaGroup.unknown()

    def test_explicit_org(self) -> None:
        qg = auth_mod._resolve_quota_group(
            "org:abc12345-6789-4abc-9def-0123456789ab",
            organization_uuid=None,
            account_id=None,
        )
        assert qg == QuotaGroup(kind="org", value="abc12345-6789-4abc-9def-0123456789ab")

    def test_invalid_kind_raises(self) -> None:

        with pytest.raises(typer.BadParameter, match="kind must be"):
            auth_mod._resolve_quota_group(
                "shard:something", organization_uuid=None, account_id=None
            )
