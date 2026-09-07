"""Tests for metaproc.commands.auth — the labeled-pool Typer group.

Uses an in-memory ``InMemoryBackend`` via BACKEND_FACTORY monkeypatch
so no live GCP is hit. The monkey-patched ClaudeCodeCliAdapter
fixtures return a fixed blob so capture_credential does not try to
read the Mac Keychain on a Linux CI runner.

The never-print-payload invariant is enforced in every test path by a
``_assert_no_blob`` helper that greps captured stdout.

"""

from __future__ import annotations

import json
import json as _json
import subprocess as _subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from typer.testing import CliRunner

import metaproc.commands.auth as auth_mod
from metaproc.adapters.base import AuthStatus, QuotaUsage
from metaproc.adapters.claude_cli import ClaudeCodeCliAdapter
from metaproc.cli import app
from metaproc.dispatch.credential_pool import (
    ConcurrentModificationError,
    EntryState,
    EntryStatus,
    PoolEntry,
    QuotaGroup,
    Vehicle,
    fingerprint_blob,
)

_SECRET_BLOB = json.dumps({"claudeAiOauth": {"accessToken": "sk-ant-oat01-SECRET-DO-NOT-PRINT"}})
_SECRET_MARKER = "SECRET-DO-NOT-PRINT"


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
def fake_pool(monkeypatch: pytest.MonkeyPatch):
    pool = _InMemoryBackend()

    def factory(_backend: str, *, pool_user: str) -> _InMemoryBackend:  # noqa: ARG001
        return pool

    monkeypatch.setattr(auth_mod, "BACKEND_FACTORY", factory)
    return pool


@pytest.fixture
def mock_capture(monkeypatch: pytest.MonkeyPatch):
    """Short-circuit ClaudeCodeCliAdapter.capture_credential so tests don't hit Keychain."""

    monkeypatch.setattr(ClaudeCodeCliAdapter, "capture_credential", lambda self: _SECRET_BLOB)


@pytest.fixture
def runner():
    # typer>=0.14 drops mix_stderr; we capture via click.Result.stderr_bytes
    # on invoke(..., catch_exceptions=False). Use default runner.
    return CliRunner()


def _assert_no_blob(stdout: str, stderr: str) -> None:
    # Never-print-payload invariant. The secret marker must not appear
    # in any user-facing stream — not stdout, not stderr.
    assert _SECRET_MARKER not in stdout, "blob leaked to stdout"
    assert _SECRET_MARKER not in stderr, "blob leaked to stderr"
    # Also guard against the accessToken key appearing verbatim — a
    # future regression where the dataclass leaks via repr would show
    # the key even without the value marker.
    assert "accessToken" not in stdout


# ── push ─────────────────────────────────────────────────────────


class TestAuthPush:
    def test_push_creates_active_entry_without_leaking_blob(self, runner, fake_pool, mock_capture):
        # Phase 10 (Vehicle A redesign): the default --vehicle for
        # claude-code-cli is now `oauth-token`. To exercise the
        # legacy Vehicle B (Keychain-backed) push path that this test
        # was written for, pass --vehicle login-credentials explicitly.
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
        _assert_no_blob(result.stdout, result.stderr)
        assert ("claude-code-cli", "laptop") in fake_pool.entries
        entry = fake_pool.entries[("claude-code-cli", "laptop")]
        assert entry.state.status == "active"
        assert entry.state.fp == fingerprint_blob(_SECRET_BLOB)
        assert entry.blob == _SECRET_BLOB  # blob stored in backend (internal), not printed

    def test_push_rejects_invalid_label(self, runner, fake_pool, mock_capture):
        result = runner.invoke(
            app,
            [
                "auth",
                "push",
                "--adapter",
                "claude-code-cli",
                "--label",
                "BAD LABEL",
                "--vehicle",
                "login-credentials",
            ],
        )
        assert result.exit_code != 0
        _assert_no_blob(result.stdout, result.stderr)

    def test_push_rejects_non_auth_capable_adapter(self, runner, fake_pool):
        result = runner.invoke(
            app,
            ["auth", "push", "--adapter", "pi-cli", "--label", "laptop"],
        )
        assert result.exit_code != 0
        assert "not auth-capable" in result.stderr or "AuthCapableCliAdapter" in result.stderr


# ── list ─────────────────────────────────────────────────────────


class TestAuthList:
    def _prepopulate(self, fake_pool):
        seeds: list[tuple[str, EntryStatus]] = [
            ("laptop", "active"),
            ("home", "cooling"),
            ("work", "disabled"),
            ("alt1", "expired"),
        ]
        for label, status in seeds:
            fake_pool.entries[("claude-code-cli", label)] = PoolEntry(
                adapter="claude-code-cli",
                label=label,
                blob=_SECRET_BLOB,
                state=EntryState(status=status, fp=f"fp-{label}", last_ok_ts=1_700_000_000),
                etag="e",
            )

    def test_list_shows_all_entries_without_blob(self, runner, fake_pool):
        self._prepopulate(fake_pool)
        result = runner.invoke(app, ["auth", "list"])
        assert result.exit_code == 0, result.stderr
        _assert_no_blob(result.stdout, result.stderr)
        # Shape: {"entries": [...]}; labels present, blob absent.
        assert "laptop" in result.stdout
        assert "home" in result.stdout
        assert "cooling" in result.stdout
        assert _SECRET_BLOB not in result.stdout

    def test_list_filter_by_status(self, runner, fake_pool):
        self._prepopulate(fake_pool)
        result = runner.invoke(app, ["auth", "list", "--status", "cooling"])
        assert result.exit_code == 0
        _assert_no_blob(result.stdout, result.stderr)
        assert "home" in result.stdout
        assert "laptop" not in result.stdout

    def test_list_rejects_unknown_status(self, runner, fake_pool):
        result = runner.invoke(app, ["auth", "list", "--status", "wat"])
        assert result.exit_code != 0

    def test_list_with_usage_does_not_print_blob(self, runner, fake_pool):
        self._prepopulate(fake_pool)
        result = runner.invoke(app, ["auth", "list", "--usage"])
        assert result.exit_code == 0
        _assert_no_blob(result.stdout, result.stderr)
        # Claude query_quota_usage returns None pending P0.4 — renders "—".
        assert "remaining" in result.stdout
        # And no plaintext blob anywhere.
        assert "accessToken" not in result.stdout


# ── quota ────────────────────────────────────────────────────────


class TestAuthQuota:
    def test_quota_uses_vehicle_a_pool_blob_without_leaking_secret(
        self,
        runner,
        fake_pool,
        monkeypatch: pytest.MonkeyPatch,
    ):
        token = "sk-ant-oat01-SECRET-DO-NOT-PRINT"
        fake_pool.entries[("claude-code-cli", "alt1")] = PoolEntry(
            adapter="claude-code-cli",
            label="alt1",
            blob=token,
            state=EntryState(
                status="active",
                fp="fp-alt1",
                vehicle=Vehicle.OAUTH_TOKEN,
            ),
            etag="e",
        )
        seen: dict[str, object] = {}

        def fake_query(
            self: ClaudeCodeCliAdapter,
            slot_dir: Path,
            *,
            vehicle=None,
            blob: str = "",
        ) -> QuotaUsage:
            seen["vehicle"] = vehicle
            seen["blob"] = blob
            seen["credential_file_exists"] = (slot_dir / self.slot_credential_filename).exists()
            return QuotaUsage(
                remaining_ratio=0.42,
                remaining_units=None,
                total_units=None,
                resets_at=None,
                unit_kind="window-utilization-live",
                detail="test quota",
            )

        monkeypatch.setattr(ClaudeCodeCliAdapter, "query_live_quota", fake_query)

        result = runner.invoke(
            app,
            ["auth", "quota", "--adapter", "claude-code-cli", "--format", "json"],
        )

        assert result.exit_code == 0, result.stderr
        _assert_no_blob(result.stdout, result.stderr)
        payload = json.loads(result.stdout)
        assert payload["entries"][0]["remaining_pct"] == 42
        assert seen == {
            "vehicle": Vehicle.OAUTH_TOKEN,
            "blob": token,
            "credential_file_exists": False,
        }


# ── enable / disable / rotate ─────────────────────────────────────


class TestAuthEnableDisable:
    def _entry(self, status: EntryStatus, fake_pool):
        fake_pool.entries[("claude-code-cli", "laptop")] = PoolEntry(
            adapter="claude-code-cli",
            label="laptop",
            blob=_SECRET_BLOB,
            state=EntryState(status=status, fp="fp-1"),
            etag="etag-0",
        )

    def test_disable_flips_active_to_disabled(self, runner, fake_pool):
        self._entry("active", fake_pool)
        result = runner.invoke(
            app,
            ["auth", "disable", "--adapter", "claude-code-cli", "--label", "laptop"],
        )
        assert result.exit_code == 0
        _assert_no_blob(result.stdout, result.stderr)
        assert fake_pool.entries[("claude-code-cli", "laptop")].state.status == "disabled"

    def test_disable_preserves_schema_v2_fields(self, runner, fake_pool):

        fake_pool.entries[("claude-code-cli", "laptop")] = PoolEntry(
            adapter="claude-code-cli",
            label="laptop",
            blob=_SECRET_BLOB,
            state=EntryState(
                status="active",
                fp="fp-va",
                vehicle=Vehicle.OAUTH_TOKEN,
                account_id="acct-1234",
                organization_uuid="org-uuid-abcd",
                quota_group=QuotaGroup(kind="org", value="org-uuid-abcd"),
            ),
            etag="etag-0",
        )
        result = runner.invoke(
            app,
            ["auth", "disable", "--adapter", "claude-code-cli", "--label", "laptop"],
        )
        assert result.exit_code == 0
        new_state = fake_pool.entries[("claude-code-cli", "laptop")].state
        assert new_state.status == "disabled"
        # Every schema-v2 field survives the status flip.
        assert new_state.vehicle == Vehicle.OAUTH_TOKEN
        assert new_state.account_id == "acct-1234"
        assert new_state.organization_uuid == "org-uuid-abcd"
        assert new_state.quota_group == QuotaGroup(kind="org", value="org-uuid-abcd")

    def test_enable_flips_disabled_to_active(self, runner, fake_pool):
        self._entry("disabled", fake_pool)
        result = runner.invoke(
            app,
            ["auth", "enable", "--adapter", "claude-code-cli", "--label", "laptop"],
        )
        assert result.exit_code == 0
        _assert_no_blob(result.stdout, result.stderr)
        assert fake_pool.entries[("claude-code-cli", "laptop")].state.status == "active"

    def test_enable_refuses_expired_entry_with_push_hint(self, runner, fake_pool):
        self._entry("expired", fake_pool)
        result = runner.invoke(
            app,
            ["auth", "enable", "--adapter", "claude-code-cli", "--label", "laptop"],
        )
        assert result.exit_code != 0
        # Operator-actionable message names the remediation command.
        assert "metaproc auth push" in result.stderr
        _assert_no_blob(result.stdout, result.stderr)

    def test_enable_already_active_refuses(self, runner, fake_pool):
        # Per docstring: "Refuses entries that are already active so the CLI is
        # operator-informative rather than silently a no-op." Emit a non-zero
        # exit and a clear message naming the remediation (none needed — the
        # label is already active).
        self._entry("active", fake_pool)
        result = runner.invoke(
            app,
            ["auth", "enable", "--adapter", "claude-code-cli", "--label", "laptop"],
        )
        assert result.exit_code != 0
        assert "already active" in result.stderr


class TestAuthRotate:
    def test_rotate_delegates_to_push_semantics(self, runner, fake_pool, mock_capture):
        # Seed an existing entry that rotate will overwrite.
        fake_pool.entries[("claude-code-cli", "laptop")] = PoolEntry(
            adapter="claude-code-cli",
            label="laptop",
            blob="OLD-PAYLOAD",
            state=EntryState(status="active", fp="old-fp"),
            etag="etag-0",
        )
        result = runner.invoke(
            app,
            ["auth", "rotate", "--adapter", "claude-code-cli", "--label", "laptop"],
        )
        assert result.exit_code == 0
        _assert_no_blob(result.stdout, result.stderr)
        entry = fake_pool.entries[("claude-code-cli", "laptop")]
        # New blob was pushed under the same label.
        assert entry.blob == _SECRET_BLOB
        assert entry.state.fp == fingerprint_blob(_SECRET_BLOB)


# ── Typer registration smoke ─────────────────────────────────────


class TestAuthGroupRegistered:
    def test_auth_help_lists_subcommands(self, runner):
        result = runner.invoke(app, ["auth", "--help"])
        assert result.exit_code == 0
        # All Phase 1/2 subcommands must be reachable under `auth`.
        for sub in ("push", "list", "enable", "disable", "rotate", "check"):
            assert sub in result.stdout

    def test_auth_is_distinct_from_claude_auth(self, runner):
        # claude-auth remains until P2.E — this is the additive shipping
        # posture. Both surfaces must be reachable.
        r1 = runner.invoke(app, ["auth", "--help"])
        r2 = runner.invoke(app, ["claude-auth", "--help"])
        assert r1.exit_code == 0
        assert r2.exit_code == 0


# ── check (P2.2) ─────────────────────────────────────────────────


class TestAuthCheck:
    def _seed(self, fake_pool, *, status: EntryStatus):
        fake_pool.entries[("claude-code-cli", "laptop")] = PoolEntry(
            adapter="claude-code-cli",
            label="laptop",
            blob=_SECRET_BLOB,
            state=EntryState(status=status, fp="fp-1"),
            etag="etag-0",
        )

    def test_check_flips_cooling_to_active_when_probe_passes(
        self, runner, fake_pool, monkeypatch, tmp_path
    ):

        self._seed(fake_pool, status="cooling")

        # Patch Claude's check_auth to return a success result (we
        # don't want the test to try to reach the real claude CLI).
        monkeypatch.setattr(
            ClaudeCodeCliAdapter,
            "check_auth",
            lambda self: AuthStatus(
                adapter_type="claude-code-cli",
                cli_found=True,
                cli_path="/usr/bin/claude",
                credentials_found=True,
                auth_mode="interactive-login",
                details="probe passed",
                setup_hint="",
            ),
        )
        result = runner.invoke(
            app,
            ["auth", "check", "--adapter", "claude-code-cli", "--label", "laptop"],
        )
        assert result.exit_code == 0, result.stderr
        _assert_no_blob(result.stdout, result.stderr)
        assert fake_pool.entries[("claude-code-cli", "laptop")].state.status == "active"

    def test_check_uses_materialized_slot_not_ambient_home_or_api_key(
        self, runner, fake_pool, monkeypatch, tmp_path
    ):
        self._seed(fake_pool, status="cooling")
        monkeypatch.setattr(
            "metaproc.adapters.claude_cli.shutil.which",
            lambda _: "/usr/bin/claude",
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "empty-home")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-live-ambient")

        result = runner.invoke(
            app,
            ["auth", "check", "--adapter", "claude-code-cli", "--label", "laptop"],
        )

        assert result.exit_code == 0, result.stderr
        _assert_no_blob(result.stdout, result.stderr)
        assert "interactive-login" in result.stdout
        assert "api-key" not in result.stdout
        assert fake_pool.entries[("claude-code-cli", "laptop")].state.status == "active"
        # The real home remains empty; check_auth succeeded only because
        # auth check scoped it to the materialized slot.
        assert not (Path.home() / ".claude").exists()

    def test_check_exits_nonzero_when_probe_fails(self, runner, fake_pool, monkeypatch):

        self._seed(fake_pool, status="cooling")
        monkeypatch.setattr(
            ClaudeCodeCliAdapter,
            "check_auth",
            lambda self: AuthStatus(
                adapter_type="claude-code-cli",
                cli_found=True,
                cli_path="/usr/bin/claude",
                credentials_found=False,
                auth_mode="none",
                details="no session",
                setup_hint="",
            ),
        )
        result = runner.invoke(
            app,
            ["auth", "check", "--adapter", "claude-code-cli", "--label", "laptop"],
        )
        assert result.exit_code != 0
        _assert_no_blob(result.stdout, result.stderr)
        # Label still cooling — check failed doesn't flip.
        assert fake_pool.entries[("claude-code-cli", "laptop")].state.status == "cooling"

    def test_check_leaves_active_label_unchanged(self, runner, fake_pool, monkeypatch):

        self._seed(fake_pool, status="active")
        monkeypatch.setattr(
            ClaudeCodeCliAdapter,
            "check_auth",
            lambda self: AuthStatus(
                adapter_type="claude-code-cli",
                cli_found=True,
                cli_path="/usr/bin/claude",
                credentials_found=True,
                auth_mode="interactive-login",
                details="ok",
                setup_hint="",
            ),
        )
        before_etag = fake_pool.entries[("claude-code-cli", "laptop")].etag
        result = runner.invoke(
            app,
            ["auth", "check", "--adapter", "claude-code-cli", "--label", "laptop"],
        )
        assert result.exit_code == 0
        # Active labels don't churn etags on check.
        assert fake_pool.entries[("claude-code-cli", "laptop")].etag == before_etag

    def test_check_errors_on_missing_entry(self, runner, fake_pool):
        result = runner.invoke(
            app,
            ["auth", "check", "--adapter", "claude-code-cli", "--label", "ghost"],
        )
        assert result.exit_code != 0


class TestAuthProbe:
    """Real-API probe via claude -d api with debug-log parsing."""

    def _seed(self, fake_pool, *, status: EntryStatus = "active"):
        fake_pool.entries[("claude-code-cli", "alt"),] = None  # type: ignore[index]
        fake_pool.entries[("claude-code-cli", "alt")] = PoolEntry(
            adapter="claude-code-cli",
            label="alt",
            blob=_SECRET_BLOB,
            state=EntryState(status=status, fp="fp-1"),
            etag="etag-0",
        )

    def _patch_subprocess(
        self,
        monkeypatch,
        *,
        returncode: int,
        stdout: str = "",
        stderr: str = "",
        debug_log: str = "",
    ):
        """Patch subprocess.run + write a fake claude-code-debug.log into the slot dir.

        The probe command runs ``claude -d api --debug-file=<slot>/claude-code-debug.log``
        — workers write the log file as a side effect of execution. To
        simulate, we capture the slot path from the cmd args and write the
        fixture log there before the probe reads it.
        """

        class _CompletedProcess:
            def __init__(self, rc, out, err):
                self.returncode = rc
                self.stdout = out
                self.stderr = err

        def fake_run(cmd, *, env, capture_output, text, timeout, check):  # noqa: ARG001
            # Find --debug-file arg, write fixture content there so probe reads it.
            try:
                debug_idx = cmd.index("--debug-file")
                slot_path = Path(cmd[debug_idx + 1])
                if debug_log:
                    slot_path.write_text(debug_log)
            except (ValueError, IndexError):
                pass
            return _CompletedProcess(returncode, stdout, stderr)

        monkeypatch.setattr(_subprocess, "run", fake_run)

    def test_probe_active_when_claude_returns_zero(self, runner, fake_pool, monkeypatch):
        self._seed(fake_pool, status="cooling")
        self._patch_subprocess(monkeypatch, returncode=0, stdout="OK\n", debug_log="")
        result = runner.invoke(
            app, ["auth", "probe", "--adapter", "claude-code-cli", "--label", "alt"]
        )
        assert result.exit_code == 0, result.stderr
        # New summary line shape: "✓ probe active  <adapter>/<label>  fp=..."
        assert "probe active" in result.stdout
        assert fake_pool.entries[("claude-code-cli", "alt")].state.status == "active"

    def test_probe_expired_on_oauth_refresh_400(self, runner, fake_pool, monkeypatch):
        # An expired OAuth credential can fail refresh first and then return
        # an authentication error from the API.
        self._seed(fake_pool, status="active")
        debug_log = (
            "2026-04-27T18:08:18Z [DEBUG] [API:auth] OAuth token check starting\n"
            "2026-04-27T18:08:18Z [ERROR] AxiosError: "
            "[url=https://platform.claude.com/v1/oauth/token, status=400] "
            "AxiosError: Request failed with status code 400\n"
            "2026-04-27T18:08:18Z [DEBUG] [API:auth] OAuth token check complete\n"
            "2026-04-27T18:08:19Z [ERROR] API error (attempt 1/11): 401 "
            '{"type":"error","error":{"type":"authentication_error",'
            '"message":"Invalid authentication credentials"},'
            '"request_id":"req_011CaUni12345"}\n'
        )
        stdout = (
            'Failed to authenticate. API Error: 401 {"type":"error","error":'
            '{"type":"authentication_error","message":"Invalid authentication credentials"},'
            '"request_id":"req_011CaUni12345"}\n'
        )
        self._patch_subprocess(monkeypatch, returncode=1, stdout=stdout, debug_log=debug_log)
        # --json forces the full record into stdout so we can assert
        # the deep-detail fields (oauth_refresh_status, error_excerpt,
        # retry_after_s) that the human summary necessarily elides.
        result = runner.invoke(
            app, ["auth", "probe", "--adapter", "claude-code-cli", "--label", "alt", "--json"]
        )
        assert result.exit_code != 0

        first = result.stdout.find("{")
        last = result.stdout.rfind("}")
        payload = _json.loads(result.stdout[first : last + 1])
        assert payload["result"] == "expired"
        assert payload["oauth_refresh_status"] == 400
        assert payload["api_status"] == 401
        assert payload["request_id"] == "req_011CaUni12345"
        assert "authentication_error" in payload["error_excerpt"]
        assert fake_pool.entries[("claude-code-cli", "alt")].state.status == "expired"

    def test_probe_cooling_on_429_with_retry_after(self, runner, fake_pool, monkeypatch):
        self._seed(fake_pool, status="active")
        debug_log = (
            "2026-04-27T18:08:18Z [ERROR] API error (attempt 1/11): 429 "
            '{"type":"error","error":{"type":"rate_limit_error",'
            '"message":"Too many requests"},"retry_after":42}\n'
        )
        self._patch_subprocess(monkeypatch, returncode=1, stdout="", debug_log=debug_log)
        result = runner.invoke(
            app,
            ["auth", "probe", "--adapter", "claude-code-cli", "--label", "alt", "--json"],
        )

        first = result.stdout.find("{")
        last = result.stdout.rfind("}")
        payload = _json.loads(result.stdout[first : last + 1])
        assert payload["result"] == "cooling"
        assert payload["api_status"] == 429

    def test_probe_unknown_when_no_signals(self, runner, fake_pool, monkeypatch):
        self._seed(fake_pool, status="active")
        self._patch_subprocess(monkeypatch, returncode=2, stdout="", debug_log="")
        result = runner.invoke(
            app, ["auth", "probe", "--adapter", "claude-code-cli", "--label", "alt"]
        )
        assert result.exit_code != 0
        # Summary line: "✗ probe unknown  ..."
        assert "probe unknown" in result.stdout

    def test_probe_emits_json_when_flag_set(self, runner, fake_pool, monkeypatch):
        self._seed(fake_pool, status="active")
        self._patch_subprocess(monkeypatch, returncode=0, stdout="OK\n", debug_log="")
        result = runner.invoke(
            app,
            ["auth", "probe", "--adapter", "claude-code-cli", "--label", "alt", "--json"],
        )
        assert result.exit_code == 0

        # JSON output is rendered as a string by OutputManager YAML rendering;
        # find the JSON object in the stdout (between first { and last }).
        first = result.stdout.find("{")
        last = result.stdout.rfind("}")
        payload = _json.loads(result.stdout[first : last + 1])
        assert payload["adapter"] == "claude-code-cli"
        assert payload["label"] == "alt"
        assert payload["result"] == "active"

    def test_probe_errors_on_missing_entry(self, runner, fake_pool):
        result = runner.invoke(
            app,
            ["auth", "probe", "--adapter", "claude-code-cli", "--label", "ghost"],
        )
        assert result.exit_code != 0

    def test_probe_recovers_from_concurrent_pool_write_race(self, runner, fake_pool, monkeypatch):

        self._seed(fake_pool, status="active")
        self._patch_subprocess(monkeypatch, returncode=0, stdout="OK\n", debug_log="")

        original_upsert = fake_pool.upsert_entry
        call_count = {"n": 0}

        def racing_upsert(adapter, label, *, blob, state, expected_etag=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Simulate a sibling probe that already advanced the entry
                # by re-stamping its etag and bumping the timestamp before
                # this probe could win the race.
                current = fake_pool.entries[(adapter, label)]
                fake_pool.entries[(adapter, label)] = PoolEntry(
                    adapter=adapter,
                    label=label,
                    blob=current.blob,
                    state=current.state,
                    etag="etag-1",
                )
                raise ConcurrentModificationError("simulated etag race")
            return original_upsert(
                adapter, label, blob=blob, state=state, expected_etag=expected_etag
            )

        monkeypatch.setattr(fake_pool, "upsert_entry", racing_upsert)
        result = runner.invoke(
            app, ["auth", "probe", "--adapter", "claude-code-cli", "--label", "alt"]
        )
        assert result.exit_code == 0, result.stderr
        # Probe completed despite the race — the post-probe state
        # reflects the freshly-read entry (active), not the crash.
        assert "probe active" in result.stdout
        # The retry path was actually exercised.
        assert call_count["n"] >= 2

    def test_probe_output_names_pool_blob_as_source(self, runner, fake_pool, monkeypatch):
        # Operators must not conflate the pool blob with their ambient
        # ~/.claude session. The probe output's ``source`` field names
        # the backend + fingerprint of the credential actually tested
        # so a stale-pool-blob false-positive is operator-actionable.
        # The summary line carries the fp; the full ``source`` field
        # is in the structured record (assert via --json).
        self._seed(fake_pool, status="active")
        self._patch_subprocess(monkeypatch, returncode=0, stdout="OK\n", debug_log="")
        result = runner.invoke(
            app,
            ["auth", "probe", "--adapter", "claude-code-cli", "--label", "alt", "--json"],
        )
        assert result.exit_code == 0

        first = result.stdout.find("{")
        last = result.stdout.rfind("}")
        payload = _json.loads(result.stdout[first : last + 1])
        assert payload["source"].startswith("pool blob")
        assert "fp-1" in payload["source"]
