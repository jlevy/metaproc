"""Tests for the auth status dashboard (plan-2026-04-21 P4.1).

Two layers:
- Pure ``build_pool_status`` composition + rendering functions — no
  Typer, no backend network.
- Typer subcommand tests using the in-memory backend fixture from
  test_auth_command (via BACKEND_FACTORY monkeypatch).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest
from typer.testing import CliRunner

import metaproc.commands.auth as auth_mod
from metaproc.cli import app
from metaproc.dispatch.credential_pool import (
    EntryState,
    EntryStatus,
    PoolEntry,
    fingerprint_blob,
)
from metaproc.dispatch.pool_status import (
    AdapterReport,
    LabelReport,
    PoolStatusReport,
    _fmt_relative,
    build_pool_status,
    render_text,
    to_json_dict,
)

# ── In-memory backend fake ──────────────────────────────────────


@dataclass
class _InMemoryBackend:
    entries: dict[tuple[str, str], PoolEntry] = field(default_factory=dict)

    def get_entry(self, adapter: str, label: str) -> PoolEntry:
        return self.entries[(adapter, label)]

    def list_entries(self, adapter: str | None = None) -> list[PoolEntry]:
        return [e for (a, _l), e in self.entries.items() if adapter is None or a == adapter]

    def upsert_entry(self, *args, **kwargs) -> str:  # noqa: ARG002 — unused by tests
        return "etag"

    def delete_entry(self, *args, **kwargs) -> None:  # noqa: ARG002
        return None

    def seed(
        self,
        adapter: str,
        label: str,
        *,
        status: EntryStatus,
        last_ok_ts: int | None = None,
        last_quota_ts: int | None = None,
        cooling_until_ts: int | None = None,
    ) -> None:
        self.entries[(adapter, label)] = PoolEntry(
            adapter=adapter,
            label=label,
            blob="{}",
            state=EntryState(
                status=status,
                fp=f"fp-{label}",
                last_ok_ts=last_ok_ts,
                last_quota_ts=last_quota_ts,
                cooling_until_ts=cooling_until_ts,
            ),
            etag="etag",
        )


@pytest.fixture
def pool():
    return _InMemoryBackend()


# ── build_pool_status ──────────────────────────────────────────


class TestBuildPoolStatus:
    def test_empty_pool_returns_empty_adapters(self, pool):
        report = build_pool_status(pool, pool_user="levy", backend_name="local")
        assert report.pool_user == "levy"
        assert report.backend == "local"
        assert report.adapters == []

    def test_single_active_label_eligible(self, pool):
        pool.seed("claude-code-cli", "laptop", status="active", last_ok_ts=1_700_000_000)
        report = build_pool_status(pool, pool_user="levy", backend_name="local", now=1_700_000_100)
        assert len(report.adapters) == 1
        a = report.adapters[0]
        assert a.adapter == "claude-code-cli"
        assert a.eligible_count == 1
        assert a.total_count == 1
        assert a.earliest_reset_ts is None
        assert a.labels[0].eligible is True
        assert a.labels[0].last_ok_age_s == 100

    def test_cooling_label_surfaces_reset_time(self, pool):
        pool.seed(
            "claude-code-cli",
            "home",
            status="cooling",
            cooling_until_ts=1_700_000_900,
        )
        report = build_pool_status(pool, pool_user="u", backend_name="local", now=1_700_000_000)
        a = report.adapters[0]
        assert a.eligible_count == 0
        assert a.earliest_reset_ts == 1_700_000_900
        assert a.earliest_reset_remaining_s == 900
        assert a.labels[0].cooling_remaining_s == 900

    def test_past_cooling_is_eligible_again(self, pool):
        # A cooling label whose cooling_until has passed becomes
        # eligible for selection again — same rule eligible_labels()
        # uses.
        pool.seed(
            "claude-code-cli",
            "home",
            status="cooling",
            cooling_until_ts=1_700_000_000,
        )
        report = build_pool_status(pool, pool_user="u", backend_name="local", now=1_700_000_500)
        a = report.adapters[0]
        # Eligible because cooling_until_ts < now, even though status
        # is still "cooling" (the selector flips it back on next probe).
        assert a.eligible_count == 1

    def test_earliest_reset_picks_min_across_cooling_labels(self, pool):
        pool.seed(
            "claude-code-cli",
            "first",
            status="cooling",
            cooling_until_ts=1_700_000_500,
        )
        pool.seed(
            "claude-code-cli",
            "second",
            status="cooling",
            cooling_until_ts=1_700_000_900,
        )
        report = build_pool_status(pool, pool_user="u", backend_name="local", now=1_700_000_000)
        assert report.adapters[0].earliest_reset_ts == 1_700_000_500

    def test_earliest_reset_ignores_non_cooling_entries(self, pool):
        # An active entry with a stale cooling_until_ts (carried
        # forward from a previous throttle) must not be surfaced as
        # the earliest reset — only currently-cooling labels count.
        pool.seed(
            "claude-code-cli",
            "laptop",
            status="active",
            cooling_until_ts=1_500_000_000,
        )
        pool.seed(
            "claude-code-cli",
            "home",
            status="cooling",
            cooling_until_ts=1_700_000_900,
        )
        report = build_pool_status(pool, pool_user="u", backend_name="local", now=1_700_000_000)
        assert report.adapters[0].earliest_reset_ts == 1_700_000_900

    def test_multiple_adapters_grouped_and_sorted(self, pool):
        pool.seed("codex-cli", "work", status="active")
        pool.seed("claude-code-cli", "laptop", status="active")
        report = build_pool_status(pool, pool_user="u", backend_name="local")
        # Adapters sorted alphabetically for stable output.
        assert [a.adapter for a in report.adapters] == ["claude-code-cli", "codex-cli"]

    def test_adapter_filter_scopes_report(self, pool):
        pool.seed("codex-cli", "work", status="active")
        pool.seed("claude-code-cli", "laptop", status="active")
        report = build_pool_status(
            pool,
            pool_user="u",
            backend_name="local",
            adapter="claude-code-cli",
        )
        assert len(report.adapters) == 1
        assert report.adapters[0].adapter == "claude-code-cli"

    def test_labels_sorted_eligible_first(self, pool):
        # Three labels, mixed states — eligible ones surface first so
        # operators scanning the output see usable credentials at the
        # top.
        pool.seed("claude-code-cli", "expired-label", status="expired")
        pool.seed("claude-code-cli", "active-label", status="active")
        pool.seed(
            "claude-code-cli",
            "cooling-label",
            status="cooling",
            cooling_until_ts=1_800_000_000,
        )
        report = build_pool_status(pool, pool_user="u", backend_name="local", now=1_700_000_000)
        ordered_labels = [lr.label for lr in report.adapters[0].labels]
        # Eligible first (active), then non-eligible sorted by status.
        assert ordered_labels[0] == "active-label"
        assert "expired-label" in ordered_labels
        assert "cooling-label" in ordered_labels


# ── Text rendering ────────────────────────────────────────────


class TestRenderText:
    def test_empty_pool_notes_no_labels(self):
        report = PoolStatusReport(pool_user="levy", backend="local", generated_at_ts=0)
        out = render_text(report)
        assert "POOL USER: levy" in out
        assert "no pool labels configured" in out

    def test_eligible_count_surfaced_in_header(self):
        report = PoolStatusReport(
            pool_user="u",
            backend="local",
            generated_at_ts=1_700_000_100,
            adapters=[
                AdapterReport(
                    adapter="claude-code-cli",
                    eligible_count=2,
                    total_count=3,
                    earliest_reset_ts=None,
                    earliest_reset_remaining_s=None,
                    labels=[
                        LabelReport(
                            adapter="claude-code-cli",
                            label="laptop",
                            status="active",
                            fp="fp-laptop",
                            eligible=True,
                            last_ok_ts=1_700_000_000,
                            last_ok_age_s=100,
                            last_quota_ts=None,
                            cooling_until_ts=None,
                            cooling_remaining_s=None,
                        ),
                    ],
                ),
            ],
        )
        out = render_text(report)
        assert "CLAUDE-CODE-CLI (2/3 eligible)" in out
        assert "laptop" in out
        assert "ACTIVE" in out
        assert "last_ok" in out

    def test_cooling_label_shows_absolute_and_relative_reset(self):
        report = PoolStatusReport(
            pool_user="u",
            backend="local",
            generated_at_ts=1_700_000_000,
            adapters=[
                AdapterReport(
                    adapter="codex-cli",
                    eligible_count=0,
                    total_count=1,
                    earliest_reset_ts=1_700_000_900,
                    earliest_reset_remaining_s=900,
                    labels=[
                        LabelReport(
                            adapter="codex-cli",
                            label="work",
                            status="cooling",
                            fp="fp-work",
                            eligible=False,
                            last_ok_ts=None,
                            last_ok_age_s=None,
                            last_quota_ts=1_699_999_000,
                            cooling_until_ts=1_700_000_900,
                            cooling_remaining_s=900,
                        ),
                    ],
                ),
            ],
        )
        out = render_text(report)
        assert "COOLING" in out
        # Absolute timestamp renders.
        assert "UTC" in out
        # Relative remaining renders.
        assert "in 15m" in out or "in 900s" in out
        # Earliest-reset line at the adapter level also renders.
        assert "earliest_reset" in out

    def test_color_injected_when_requested(self):
        report = PoolStatusReport(
            pool_user="u",
            backend="local",
            generated_at_ts=0,
            adapters=[
                AdapterReport(
                    adapter="claude-code-cli",
                    eligible_count=1,
                    total_count=1,
                    earliest_reset_ts=None,
                    earliest_reset_remaining_s=None,
                    labels=[
                        LabelReport(
                            adapter="claude-code-cli",
                            label="laptop",
                            status="active",
                            fp="fp",
                            eligible=True,
                            last_ok_ts=None,
                            last_ok_age_s=None,
                            last_quota_ts=None,
                            cooling_until_ts=None,
                            cooling_remaining_s=None,
                        ),
                    ],
                ),
            ],
        )
        out_color = render_text(report, use_color=True)
        out_plain = render_text(report, use_color=False)
        # ANSI green for active, absent when color suppressed.
        assert "\x1b[32m" in out_color
        assert "\x1b[32m" not in out_plain


class TestFormatRelative:
    def test_none_returns_dash(self):
        assert _fmt_relative(None) == "—"

    def test_seconds(self):
        assert _fmt_relative(45) == "in 45s"
        assert _fmt_relative(45, past=True) == "45s ago"

    def test_minutes(self):
        assert _fmt_relative(120) == "in 2m"

    def test_hours_with_minutes(self):
        # 1h 29m for readability, not 89m.
        assert "1h 29m" in _fmt_relative(3600 + 29 * 60)

    def test_hours_no_minutes(self):
        assert _fmt_relative(3600) == "in 1h"

    def test_days(self):
        assert "1d" in _fmt_relative(86400)


# ── to_json_dict ──────────────────────────────────────────────


class TestToJsonDict:
    def test_roundtrips_through_json(self, pool):
        pool.seed("claude-code-cli", "laptop", status="active", last_ok_ts=1_700_000_000)
        pool.seed(
            "claude-code-cli",
            "home",
            status="cooling",
            cooling_until_ts=1_700_000_900,
        )
        report = build_pool_status(pool, pool_user="u", backend_name="local", now=1_700_000_100)
        d = to_json_dict(report)
        # Round-trip through JSON without losing schema.
        text = json.dumps(d)
        back = json.loads(text)
        assert back["pool_user"] == "u"
        assert back["backend"] == "local"
        assert len(back["adapters"]) == 1
        assert back["adapters"][0]["eligible_count"] == 1
        assert back["adapters"][0]["total_count"] == 2
        # Absolute timestamps preserved for automation consumers.
        assert back["adapters"][0]["earliest_reset_ts"] == 1_700_000_900


# ── Typer subcommand ─────────────────────────────────────────


@pytest.fixture
def fake_pool(monkeypatch):
    pool = _InMemoryBackend()

    def factory(_backend: str, *, pool_user: str) -> _InMemoryBackend:  # noqa: ARG001
        return pool

    monkeypatch.setattr(auth_mod, "BACKEND_FACTORY", factory)
    return pool


@pytest.fixture
def runner():
    return CliRunner()


class TestAuthStatusCommand:
    def test_empty_pool_exits_zero(self, runner, fake_pool):
        result = runner.invoke(app, ["auth", "status"])
        assert result.exit_code == 0, result.stderr
        assert "no pool labels configured" in result.stdout

    def test_shows_per_adapter_rollup(self, runner, fake_pool):
        fake_pool.seed("claude-code-cli", "laptop", status="active")
        fake_pool.seed(
            "claude-code-cli",
            "home",
            status="cooling",
            cooling_until_ts=1_800_000_000,
        )
        fake_pool.seed("codex-cli", "work", status="active")
        result = runner.invoke(app, ["auth", "status"])
        assert result.exit_code == 0, result.stderr
        assert "CLAUDE-CODE-CLI" in result.stdout
        assert "CODEX-CLI" in result.stdout
        assert "laptop" in result.stdout
        assert "home" in result.stdout
        assert "work" in result.stdout

    def test_json_output_schema(self, runner, fake_pool):
        fake_pool.seed("claude-code-cli", "laptop", status="active")
        result = runner.invoke(app, ["auth", "status", "--json"])
        assert result.exit_code == 0, result.stderr
        # JSON block present in stdout.
        data = json.loads(result.stdout)
        assert data["adapters"][0]["adapter"] == "claude-code-cli"
        assert data["adapters"][0]["total_count"] == 1

    def test_adapter_filter(self, runner, fake_pool):
        fake_pool.seed("claude-code-cli", "laptop", status="active")
        fake_pool.seed("codex-cli", "work", status="active")
        result = runner.invoke(app, ["auth", "status", "--adapter", "claude-code-cli"])
        assert result.exit_code == 0
        assert "CLAUDE-CODE-CLI" in result.stdout
        assert "CODEX-CLI" not in result.stdout

    def test_does_not_leak_blob(self, runner, fake_pool):
        # Never-print-payload invariant: status must never emit the
        # blob even though it has access to it via list_entries.
        fake_pool.entries[("claude-code-cli", "laptop")] = PoolEntry(
            adapter="claude-code-cli",
            label="laptop",
            blob="SECRET-BLOB-DO-NOT-PRINT",
            state=EntryState(status="active", fp=fingerprint_blob("x")),
            etag="e",
        )
        result = runner.invoke(app, ["auth", "status"])
        assert "SECRET-BLOB-DO-NOT-PRINT" not in result.stdout
        assert "SECRET-BLOB-DO-NOT-PRINT" not in result.stderr

    def test_no_color_flag_suppresses_ansi(self, runner, fake_pool):
        fake_pool.seed("claude-code-cli", "laptop", status="active")
        # CliRunner stdout is not a TTY, so color should be off by
        # default. --no-color is a belt-and-suspenders option that
        # must also suppress it when stdout IS a TTY.
        result = runner.invoke(app, ["auth", "status", "--no-color"])
        assert result.exit_code == 0
        assert "\x1b[" not in result.stdout

    def test_status_subcommand_registered(self, runner):
        result = runner.invoke(app, ["auth", "--help"])
        assert result.exit_code == 0
        assert "status" in result.stdout
