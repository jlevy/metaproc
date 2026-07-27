"""Tests for the auth_usage renderers.

Pure-function tests over LabelUsage shapes — no Typer harness, no
file I/O. CLI integration is covered separately via the existing
auth-command test suite.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from metaproc.dispatch.auth_usage import LabelUsage, PoolStateLiteral, RateLimitWindow
from metaproc.dispatch.auth_usage_render import (
    doctor_report_to_dict,
    label_usage_to_dict,
    render_doctor_text,
    render_run_usage_text,
    usage_report_to_dict,
)


def _make_usage(
    label: str = "alt1",
    *,
    pool_state: str = "active",
    invocations_total: int = 0,
    invocations_ok: int = 0,
    invocations_failed: int = 0,
    known_bugs: dict[str, int] | None = None,
    five_hour: RateLimitWindow | None = None,
    seven_day: RateLimitWindow | None = None,
    last_used_ts: datetime | None = None,
) -> LabelUsage:
    return LabelUsage(
        adapter="claude-code-cli",
        label=label,
        vehicle="claude_code_oauth_token",
        fingerprint=f"fp-{label}",
        pool_state=cast(PoolStateLiteral, pool_state),
        cooling_until=None,
        last_ok_ts=None,
        last_quota_ts=None,
        one_hour=None,
        five_hour=five_hour,
        seven_day=seven_day,
        last_event_ts=None,
        invocations_total=invocations_total,
        invocations_ok=invocations_ok,
        invocations_failed=invocations_failed,
        known_bugs=known_bugs or {},
        last_used_ts=last_used_ts,
        last_run_id=None,
        probe_state=None,
        probe_request_id=None,
        probe_age_s=None,
    )


# ── render_run_usage_text ───────────────────────────────────────


class TestRenderRunUsageText:
    def test_empty_usage_renders_no_events_line(self):
        out = render_run_usage_text(run_dir="/tmp/run-x", usage_by_label={})
        assert "no auth-pool events recorded" in out
        assert "/tmp/run-x" in out

    def test_239_0_0_distribution_renders_load_balance_warning(self):
        # The P0-10 fingerprint: alt1 hammered, alt2 untouched.
        usage = {
            ("claude-code-cli", "alt1"): _make_usage(
                "alt1",
                invocations_total=239,
                invocations_ok=53,
                invocations_failed=184,
                known_bugs={"claude-startup-exit-1-silent": 184},
            ),
            ("claude-code-cli", "alt2"): _make_usage("alt2", invocations_total=0),
        }
        out = render_run_usage_text(run_dir="runs/x", usage_by_label=usage)
        assert "alt1" in out
        assert "239 invocations" in out
        assert "claude-startup-exit-1-silent × 184" in out
        assert "never used" in out  # alt2's zero-invocation rendering
        assert "LOAD-BALANCING CHECK" in out
        assert "alt2" in out

    def test_rate_limit_windows_render_when_present(self):
        usage = {
            ("claude-code-cli", "alt1"): _make_usage(
                "alt1",
                invocations_total=100,
                invocations_ok=100,
                five_hour=RateLimitWindow(
                    kind="five_hour",
                    status="rejected",
                    utilization=1.0,
                    resets_at=1_770_000_000,
                ),
                seven_day=RateLimitWindow(
                    kind="seven_day",
                    status="allowed_warning",
                    utilization=0.94,
                    resets_at=1_770_010_000,
                ),
            ),
        }
        out = render_run_usage_text(run_dir="runs/x", usage_by_label=usage)
        assert "5h:" in out
        assert "7d:" in out
        assert "rejected" in out
        assert "100%" in out  # 5h utilization
        assert "94%" in out  # 7d utilization

    def test_config_block_renders_when_provided(self):
        usage = {("claude-code-cli", "alt1"): _make_usage("alt1", invocations_total=5)}
        out = render_run_usage_text(
            run_dir="runs/x",
            usage_by_label=usage,
            config={"auth_include_labels": ["alt1", "alt2"], "auth_policy": "round-robin"},
        )
        assert "CONFIG SNAPSHOT" in out
        assert "auth_include_labels" in out
        assert "round-robin" in out


# ── render_doctor_text ──────────────────────────────────────────


class TestRenderDoctorText:
    def test_clean_pool_renders_no_inconsistencies(self):
        usage = {
            ("claude-code-cli", "alt1"): _make_usage(
                "alt1", invocations_total=10, invocations_ok=10
            )
        }
        out = render_doctor_text(usage_by_label=usage)
        assert "no inconsistencies detected" in out
        assert "metaproc auth doctor" in out

    def test_unused_active_label_flagged(self):
        usage = {
            ("claude-code-cli", "alt2"): _make_usage(
                "alt2", pool_state="active", invocations_total=0
            )
        }
        out = render_doctor_text(usage_by_label=usage)
        assert "alt2" in out
        assert "0 invocations" in out
        # Hint is present when --probe wasn't requested.
        assert "--probe" in out

    def test_probe_requested_omits_skipped_line(self):
        usage = {
            ("claude-code-cli", "alt1"): _make_usage("alt1", invocations_total=5, invocations_ok=5)
        }
        out = render_doctor_text(usage_by_label=usage, probe_requested=True)
        assert "SKIPPED" not in out

    def test_probe_default_renders_skipped_line(self):
        usage = {
            ("claude-code-cli", "alt1"): _make_usage("alt1", invocations_total=5, invocations_ok=5)
        }
        out = render_doctor_text(usage_by_label=usage)
        assert "SKIPPED" in out


# ── JSON serialization ─────────────────────────────────────────


class TestLabelUsageToDict:
    def test_minimal_label(self):
        u = _make_usage("alt1", invocations_total=3, invocations_ok=2, invocations_failed=1)
        d = label_usage_to_dict(u)
        assert d["adapter"] == "claude-code-cli"
        assert d["label"] == "alt1"
        assert d["invocations"] == {"total": 3, "ok": 2, "failed": 1}
        assert d["pool_state"] == "active"
        # Unset windows serialize as None, not omitted.
        assert d["five_hour"] is None
        assert d["seven_day"] is None
        assert d["probe"]["state"] is None

    def test_datetimes_serialize_as_iso_strings(self):
        ts = datetime(2026, 5, 4, 17, 14, tzinfo=UTC)
        u = _make_usage(last_used_ts=ts)
        d = label_usage_to_dict(u)
        assert d["last_used_ts"] is not None
        assert d["last_used_ts"].startswith("2026-05-04")

    def test_window_serialization_round_trip(self):
        u = _make_usage(
            five_hour=RateLimitWindow(
                kind="five_hour",
                status="rejected",
                utilization=1.0,
                resets_at=1_770_000_000,
            )
        )
        d = label_usage_to_dict(u)
        assert d["five_hour"] == {
            "status": "rejected",
            "utilization": 1.0,
            "resets_at": 1_770_000_000,
        }


class TestReportShapes:
    def test_usage_report_lists_labels_sorted(self):
        usage = {
            ("claude-code-cli", "alt2"): _make_usage("alt2"),
            ("claude-code-cli", "alt1"): _make_usage("alt1"),
        }
        report = usage_report_to_dict(run_dir="runs/x", usage_by_label=usage)
        assert [lbl["label"] for lbl in report["labels"]] == ["alt1", "alt2"]

    def test_doctor_report_aggregates_issues(self):
        usage = {
            ("claude-code-cli", "alt1"): _make_usage(
                "alt1", invocations_total=10, invocations_ok=10
            ),
            ("claude-code-cli", "alt2"): _make_usage(
                "alt2", invocations_total=0, pool_state="active"
            ),
        }
        report = doctor_report_to_dict(usage_by_label=usage)
        assert report["labels_total"] == 2
        # alt1 clean, alt2 has the load-balance warning.
        assert report["issues_total"] == 1
        assert "claude-code-cli/alt2" in report["issues_by_label"]
