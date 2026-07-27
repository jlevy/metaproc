"""Tests for ``metaproc auth usage`` and ``metaproc auth doctor`` commands.

Spec / bead: the fix (Phase 2 of plan-2026-05-03).

End-to-end via Typer's CliRunner. Reuses the in-memory backend fake
pattern from test_auth_command (duplicated here to keep tests
self-contained per the project convention).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import metaproc.commands.auth as auth_mod
from metaproc.cli import app
from metaproc.commands import auth as _auth
from metaproc.dispatch.credential_pool import (
    EntryState,
    PoolEntry,
    Vehicle,
)
from metaproc.paths import runpool_step_events

# ── In-memory backend fake (mirrors test_auth_command) ──────────


@dataclass
class _InMemoryBackend:
    entries: dict[tuple[str, str], PoolEntry] = field(default_factory=dict)

    def list_entries(self, adapter: str | None = None) -> list[PoolEntry]:
        return [e for (a, _l), e in self.entries.items() if adapter is None or a == adapter]

    def get_entry(self, adapter: str, label: str) -> PoolEntry:
        return self.entries[(adapter, label)]

    def upsert_entry(self, adapter, label, *, blob, state, expected_etag=None):  # noqa: ANN001, ARG002
        self.entries[(adapter, label)] = PoolEntry(
            adapter=adapter, label=label, blob=blob or "", state=state, etag="et-1"
        )
        return "et-1"

    def delete_entry(self, adapter, label):  # noqa: ANN001
        self.entries.pop((adapter, label), None)


def _seed_active(pool: _InMemoryBackend, adapter: str, label: str) -> None:
    pool.entries[(adapter, label)] = PoolEntry(
        adapter=adapter,
        label=label,
        blob="blob",
        state=EntryState(
            status="active",
            fp=f"fp-{label}",
            last_ok_ts=1_700_000_000,
            vehicle=Vehicle.OAUTH_TOKEN,
        ),
        etag="e1",
    )


@pytest.fixture
def fake_pool(monkeypatch: pytest.MonkeyPatch) -> _InMemoryBackend:
    pool = _InMemoryBackend()

    def factory(_backend: str, *, pool_user: str) -> _InMemoryBackend:  # noqa: ARG001
        return pool

    monkeypatch.setattr(auth_mod, "BACKEND_FACTORY", factory)
    return pool


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _write_runpool_events(run_dir: Path, step: str, events: list[dict[str, Any]]) -> None:
    path = runpool_step_events(run_dir, step)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")


def _outcome(label: str, classification: str = "ok") -> dict[str, Any]:
    return {
        "event": "auth_outcome",
        "schema_version": 2,
        "ts": "2026-05-04T12:00:00+00:00",
        "adapter": "claude-code-cli",
        "label": label,
        "classification": classification,
        "run_id": "run-x",
        "step_id": "predict-ticker",
        "item": "AAPL",
        "attempt": 0,
        "session_log_path": "",
        "known_bug_signature": "",
    }


# ── auth usage ───────────────────────────────────────────────────


class TestAuthUsageCommand:
    def test_empty_run_dir(self, runner: CliRunner, fake_pool: _InMemoryBackend, tmp_path: Path):
        del fake_pool
        result = runner.invoke(app, ["auth", "usage", str(tmp_path)])
        assert result.exit_code == 0
        assert "no auth-pool events recorded" in result.stdout

    def test_renders_per_label_attribution(
        self, runner: CliRunner, fake_pool: _InMemoryBackend, tmp_path: Path
    ):
        _seed_active(fake_pool, "claude-code-cli", "alt1")
        _seed_active(fake_pool, "claude-code-cli", "alt2")
        events = [_outcome("alt1") for _ in range(3)] + [_outcome("alt2")]
        _write_runpool_events(tmp_path, "predict-ticker", events)
        result = runner.invoke(app, ["auth", "usage", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "alt1" in result.stdout
        assert "3 invocations" in result.stdout
        assert "1 invocations" in result.stdout

    def test_json_format(self, runner: CliRunner, fake_pool: _InMemoryBackend, tmp_path: Path):
        _seed_active(fake_pool, "claude-code-cli", "alt1")
        _write_runpool_events(tmp_path, "step", [_outcome("alt1")])
        result = runner.invoke(app, ["auth", "usage", str(tmp_path), "--format", "json"])
        assert result.exit_code == 0, result.output
        report = json.loads(result.stdout)
        assert report["run_dir"] == str(tmp_path)
        assert report["labels"][0]["label"] == "alt1"
        assert report["labels"][0]["invocations"]["total"] == 1

    def test_invalid_format_rejected(
        self, runner: CliRunner, fake_pool: _InMemoryBackend, tmp_path: Path
    ):
        del fake_pool
        result = runner.invoke(app, ["auth", "usage", str(tmp_path), "--format", "csv"])
        assert result.exit_code != 0


# ── auth doctor ──────────────────────────────────────────────────


class TestAuthDoctorCommand:
    def test_no_run_dir_pool_only(self, runner: CliRunner, fake_pool: _InMemoryBackend):
        _seed_active(fake_pool, "claude-code-cli", "alt1")
        result = runner.invoke(app, ["auth", "doctor"])
        assert result.exit_code == 0, result.output
        assert "metaproc auth doctor" in result.stdout

    def test_unused_active_label_flagged(self, runner: CliRunner, fake_pool: _InMemoryBackend):
        # alt1 active in pool, never used in the run.
        _seed_active(fake_pool, "claude-code-cli", "alt1")
        result = runner.invoke(app, ["auth", "doctor"])
        assert result.exit_code == 0
        # The label active-but-zero-invocations rule fires.
        assert "alt1" in result.stdout

    def test_with_run_dir_aggregates_events(
        self, runner: CliRunner, fake_pool: _InMemoryBackend, tmp_path: Path
    ):
        _seed_active(fake_pool, "claude-code-cli", "alt1")
        _seed_active(fake_pool, "claude-code-cli", "alt2")
        # alt1 used heavily, alt2 untouched — the P0-10 fingerprint.
        events = [_outcome("alt1") for _ in range(10)]
        _write_runpool_events(tmp_path, "step", events)
        result = runner.invoke(app, ["auth", "doctor", "--run-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "alt2" in result.stdout

    def test_skipped_line_present_without_probe(
        self, runner: CliRunner, fake_pool: _InMemoryBackend
    ):
        _seed_active(fake_pool, "claude-code-cli", "alt1")
        result = runner.invoke(app, ["auth", "doctor"])
        assert "SKIPPED" in result.stdout

    def test_probe_flag_invokes_probe_helper_per_label(
        self,
        runner: CliRunner,
        fake_pool: _InMemoryBackend,
        monkeypatch: pytest.MonkeyPatch,
    ):
        # review (P2): --probe used to be a no-op. Now it
        # invokes _probe_label_state per label and the result lands
        # on LabelUsage.probe_state. Stub the helper so this test
        # doesn't shell out to ``claude``.
        _seed_active(fake_pool, "claude-code-cli", "alt1")
        _seed_active(fake_pool, "claude-code-cli", "alt2")

        called_labels: list[tuple[str, str]] = []

        def _stub_probe(adapter_impl, pool, adapter, label):  # noqa: ANN001, ARG001
            called_labels.append((adapter, label))

            return _auth._LiveProbeOutcome(  # noqa: SLF001
                state="active", request_id="req_x", api_status=200, note="ok"
            )

        monkeypatch.setattr(auth_mod, "_probe_label_state", _stub_probe)
        # get_auth_capable would otherwise return None for the fake
        # adapter — return a sentinel so the loop reaches the helper.
        monkeypatch.setattr(auth_mod, "get_auth_capable", lambda _name: object())

        result = runner.invoke(app, ["auth", "doctor", "--probe"])
        assert result.exit_code == 0, result.output
        # Both labels were probed.
        assert ("claude-code-cli", "alt1") in called_labels
        assert ("claude-code-cli", "alt2") in called_labels

    def test_json_format(self, runner: CliRunner, fake_pool: _InMemoryBackend):
        _seed_active(fake_pool, "claude-code-cli", "alt1")
        result = runner.invoke(app, ["auth", "doctor", "--format", "json"])
        assert result.exit_code == 0, result.output
        report = json.loads(result.stdout)
        assert "labels_total" in report
        assert "issues_total" in report
        assert "probe_requested" in report
