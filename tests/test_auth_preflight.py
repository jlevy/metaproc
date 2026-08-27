"""Tests for the fix: auth preflight CLI + query_quota_usage wiring.

Spec: src/metaproc/docs/metaproc-design.md
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from typer.testing import CliRunner

import metaproc.commands.auth as auth_mod
from metaproc.adapters.claude_code import ClaudeCodeCliAdapter
from metaproc.cli import app
from metaproc.dispatch.auth_usage import query_label_headroom
from metaproc.dispatch.credential_pool import (
    EntryState,
    PoolEntry,
    Vehicle,
)

# ── Helpers ─────────────────────────────────────────────────────


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


def _write_run_with_rate_limit(
    runs_dir: Path,
    *,
    run_name: str,
    label: str,
    five_hour_util: float = 0.5,
    seven_day_util: float = 0.3,
) -> Path:
    """Build a run dir with a lease event + a session JSONL with rate_limit_event."""
    run_dir = runs_dir / run_name
    log_dir = run_dir / "predict-ticker" / ".logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    session = log_dir / "predict_AAPL.jsonl"
    session.write_text(
        json.dumps(
            {
                "type": "rate_limit_event",
                "ts": "2026-05-04T17:00:00+00:00",
                "rate_limit_info": {
                    "rateLimitType": "five_hour",
                    "status": "allowed_warning",
                    "utilization": five_hour_util,
                    "resetsAt": 1_770_000_000,
                },
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "rate_limit_event",
                "ts": "2026-05-04T17:00:01+00:00",
                "rate_limit_info": {
                    "rateLimitType": "seven_day",
                    "status": "allowed",
                    "utilization": seven_day_util,
                    "resetsAt": 1_770_010_000,
                },
            }
        )
        + "\n"
    )
    events_path = log_dir / "runpool-events.jsonl"
    events_path.write_text(
        json.dumps(
            {
                "event": "auth_lease_acquired",
                "schema_version": 2,
                "ts": "2026-05-04T16:59:00+00:00",
                "adapter": "claude-code-cli",
                "label": label,
                "slot_dir": "/tmp/slot",
                "run_id": run_name,
                "step_id": "predict-ticker",
                "item": "AAPL",
                "attempt": 0,
                "session_log_path": str(session),
                "policy": "round-robin",
            }
        )
        + "\n"
    )
    return run_dir


# ── query_label_headroom ────────────────────────────────────────


class TestQueryLabelHeadroom:
    def test_no_runs_returns_none(self, tmp_path: Path):
        five, seven = query_label_headroom("claude-code-cli", "alt1", tmp_path)
        assert five is None
        assert seven is None

    def test_finds_recent_window(self, tmp_path: Path):
        _write_run_with_rate_limit(tmp_path, run_name="run-a", label="alt1", five_hour_util=0.85)
        five, seven = query_label_headroom("claude-code-cli", "alt1", tmp_path)
        assert five is not None
        assert five.utilization == 0.85
        assert seven is not None

    def test_label_filter_isolates(self, tmp_path: Path):
        _write_run_with_rate_limit(tmp_path, run_name="run-a", label="alt1", five_hour_util=0.85)
        five, _ = query_label_headroom("claude-code-cli", "alt2", tmp_path)
        # alt2 was never used, no rate-limit data.
        assert five is None


# ── claude_code.query_quota_usage ───────────────────────────────


class TestClaudeCodeQueryQuotaUsage:
    def test_returns_none_without_runs_dir(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("RUNS_DIR", raising=False)
        adapter = ClaudeCodeCliAdapter()
        usage = adapter.query_quota_usage(Path("/nonexistent/alt1"))
        assert usage is None

    def test_returns_none_when_no_recent_data(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("RUNS_DIR", str(tmp_path))
        adapter = ClaudeCodeCliAdapter()
        usage = adapter.query_quota_usage(Path("/nonexistent/alt-never-used"))
        assert usage is None

    def test_synthesizes_usage_from_recent_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        _write_run_with_rate_limit(
            tmp_path,
            run_name="run-x",
            label="alt1",
            five_hour_util=0.8,
            seven_day_util=0.2,
        )
        monkeypatch.setenv("RUNS_DIR", str(tmp_path))
        adapter = ClaudeCodeCliAdapter()
        usage = adapter.query_quota_usage(Path("/sentinel/alt1"))
        assert usage is not None
        # 1.0 - max(0.8, 0.2) = 0.2
        assert usage.remaining_ratio is not None
        assert abs(usage.remaining_ratio - 0.2) < 1e-6
        assert usage.unit_kind == "window-utilization"
        assert "binding=5h" in usage.detail


# ── metaproc auth preflight CLI ─────────────────────────────────


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


class TestAuthPreflightCommand:
    def test_runs_with_no_pool_data(self, runner: CliRunner, fake_pool: _InMemoryBackend):
        del fake_pool
        result = runner.invoke(app, ["auth", "preflight", "--estimated-items", "10"])
        # Empty pool → unknown headroom; preflight defaults to warn
        # mode and returns "go" (it can't prove trouble).
        assert result.exit_code == 0, result.output

    def test_returns_warn_when_demand_exceeds_headroom(
        self,
        runner: CliRunner,
        fake_pool: _InMemoryBackend,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        # One label active; recent rate_limit_event shows 100% 5h
        # utilization → projected demand vastly exceeds remaining.
        _seed_active(fake_pool, "claude-code-cli", "alt1")
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        _write_run_with_rate_limit(
            runs_dir,
            run_name="r",
            label="alt1",
            five_hour_util=1.0,
            seven_day_util=0.5,
        )
        monkeypatch.setenv("RUNS_DIR", str(runs_dir))
        result = runner.invoke(
            app,
            ["auth", "preflight", "--estimated-items", "100", "--per-item-tokens", "1000"],
        )
        assert result.exit_code == 0, result.output
        # The rendered message names the verdict; in warn mode it
        # should mention status=warn or status=go (depending on
        # whether projected exceeds 80% of remaining).
        assert "preflight quota gate" in result.output

    def test_json_format(self, runner: CliRunner, fake_pool: _InMemoryBackend):
        _seed_active(fake_pool, "claude-code-cli", "alt1")
        result = runner.invoke(
            app,
            [
                "auth",
                "preflight",
                "--estimated-items",
                "10",
                "--per-item-tokens",
                "1000",
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 0, result.output
        report = json.loads(result.output)
        assert report["adapter"] == "claude-code-cli"
        assert report["estimated_items"] == 10
        assert report["verdict"] in ("go", "warn", "refuse")
        assert "projected_units" in report
        assert "headroom" in report
