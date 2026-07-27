"""Tests for `metaproc pool events --summary` aggregation.

Covers the pure summary helpers in :mod:`metaproc.commands.pool` —
no Typer / file IO. Both the auth_outcome specialization and the
generic per-event-type fallback are exercised against fixture event
streams shaped like the live ``runpool-events.jsonl`` format.
"""

from __future__ import annotations

from metaproc.commands.pool import _summarize_events


def _auth_outcome(label: str, *, classification: str = "ok", retry: int = 0, rotated: bool = False):
    return {
        "event": "auth_outcome",
        "label": label,
        "classification": classification,
        "retry_count": retry,
        "rotated": rotated,
        "fallback_policy": "same-provider",
        "ts": "2026-04-27T17:00:00+00:00",
    }


class TestSummarizeAuthOutcomes:
    def test_per_label_counts(self):
        events = [
            _auth_outcome("alt1"),
            _auth_outcome("alt1"),
            _auth_outcome("alt2"),
        ]
        report = _summarize_events(events, total_in_file=3, event_type="auth_outcome")
        assert report["matched"] == 3
        assert report["auth_outcome"]["by_label"] == {"alt1": 2, "alt2": 1}

    def test_per_classification_counts(self):
        events = [
            _auth_outcome("alt1", classification="ok"),
            _auth_outcome("alt2", classification="cooling"),
            _auth_outcome("alt2", classification="cooling"),
        ]
        report = _summarize_events(events, total_in_file=3, event_type="auth_outcome")
        assert report["auth_outcome"]["by_classification"] == {"ok": 1, "cooling": 2}

    def test_per_label_classification_breakdown(self):
        # Cross-section: alt1 always ok, alt2 mixed. Useful when one
        # label is degenerating and operators want the per-label slice.
        events = [
            _auth_outcome("alt1", classification="ok"),
            _auth_outcome("alt2", classification="ok"),
            _auth_outcome("alt2", classification="cooling"),
            _auth_outcome("alt2", classification="cooling"),
        ]
        report = _summarize_events(events, total_in_file=4, event_type="auth_outcome")
        per_label = report["auth_outcome"]["by_label_classification"]
        assert per_label["alt1"] == {"ok": 1}
        assert per_label["alt2"] == {"ok": 1, "cooling": 2}

    def test_retry_distribution(self):
        events = [
            _auth_outcome("alt1", retry=0),
            _auth_outcome("alt1", retry=0),
            _auth_outcome("alt2", retry=1),
            _auth_outcome("alt2", retry=2),
        ]
        report = _summarize_events(events, total_in_file=4, event_type="auth_outcome")
        # Sorted ascending so high-retry tails read at-a-glance.
        assert list(report["auth_outcome"]["retry_distribution"].items()) == [
            (0, 2),
            (1, 1),
            (2, 1),
        ]

    def test_rotation_count(self):
        events = [
            _auth_outcome("alt1", rotated=False),
            _auth_outcome("alt1", rotated=True),
            _auth_outcome("alt2", rotated=False),
        ]
        report = _summarize_events(events, total_in_file=3, event_type="auth_outcome")
        assert report["auth_outcome"]["rotated"] == 1

    def test_window_timestamps(self):
        events = [
            {**_auth_outcome("alt1"), "ts": "2026-04-27T12:00:00+00:00"},
            {**_auth_outcome("alt1"), "ts": "2026-04-27T13:30:00+00:00"},
            {**_auth_outcome("alt2"), "ts": "2026-04-27T14:00:00+00:00"},
        ]
        report = _summarize_events(events, total_in_file=3, event_type="auth_outcome")
        assert report["first_ts"] == "2026-04-27T12:00:00+00:00"
        assert report["last_ts"] == "2026-04-27T14:00:00+00:00"

    def test_http_axis_rollup(self):
        # HTTP-axis aggregation: the leading indicator of cohort loss is
        # rising counts of oauth_refresh_status >= 400 on a label. Rollup
        # surfaces by_api_status / by_oauth_refresh_status counters and
        # a per-label cross-section so operators can see "alt1 is taking
        # all the OAuth-refresh-400s" at a glance.
        events = [
            {**_auth_outcome("alt1", classification="ok"), "api_status": None},
            {
                **_auth_outcome("alt1", classification="expired"),
                "api_status": 401,
                "oauth_refresh_status": 400,
            },
            {
                **_auth_outcome("alt1", classification="expired"),
                "api_status": 401,
                "oauth_refresh_status": 400,
            },
            {**_auth_outcome("alt2", classification="ok"), "api_status": None},
            {
                **_auth_outcome("alt2", classification="cooling"),
                "api_status": 429,
                "retry_after_s": 30,
            },
        ]
        report = _summarize_events(events, total_in_file=5, event_type="auth_outcome")
        ao = report["auth_outcome"]
        assert ao["by_api_status"] == {"401": 2, "429": 1}
        assert ao["by_oauth_refresh_status"] == {"400": 2}
        # Per-label cross-section: alt1 has all the refresh-400s.
        assert ao["by_label_oauth_refresh"] == {"alt1": {"400": 2}}

    def test_http_axis_absent_when_all_signals_none(self):
        # Successful runs have api_status / oauth_refresh_status = None.
        # The rollup should return empty dicts (not break) and the
        # renderer should skip the section.
        events = [
            _auth_outcome("alt1", classification="ok"),
            _auth_outcome("alt2", classification="ok"),
        ]
        report = _summarize_events(events, total_in_file=2, event_type="auth_outcome")
        assert report["auth_outcome"]["by_api_status"] == {}
        assert report["auth_outcome"]["by_oauth_refresh_status"] == {}


class TestSummarizeGeneric:
    def test_counts_by_event_type_when_no_filter(self):
        # Mirrors the actual mix from a long-running pool.
        events = [
            {"event": "pool_start", "ts": "T1"},
            {"event": "process_start", "label": "ticker=NVS", "ts": "T2"},
            {"event": "process_exit", "label": "ticker=NVS", "ts": "T3"},
            {"event": "concurrency_adjust", "ts": "T4"},
            {"event": "pressure_check", "ts": "T5"},
            {"event": "pressure_check", "ts": "T6"},
        ]
        report = _summarize_events(events, total_in_file=6, event_type=None)
        assert report["matched"] == 6
        assert report["by_event"] == {
            "pool_start": 1,
            "process_start": 1,
            "process_exit": 1,
            "concurrency_adjust": 1,
            "pressure_check": 2,
        }

    def test_empty_stream_reports_zero_matched(self):
        # Filter that excludes everything still produces a coherent
        # summary (no first_ts/last_ts, just matched=0).
        report = _summarize_events([], total_in_file=42, event_type="auth_outcome")
        assert report["matched"] == 0
        assert report["total_in_file"] == 42
        assert "first_ts" not in report
        assert "auth_outcome" not in report


# ── auth_lease_acquired specialization ──


def _lease_event(label: str, *, policy: str = "round-robin"):
    return {
        "event": "auth_lease_acquired",
        "schema_version": 2,
        "adapter": "claude-code-cli",
        "label": label,
        "policy": policy,
        "run_id": "run-x",
        "step_id": "predict-ticker",
        "ts": "2026-05-05T17:00:00+00:00",
    }


class TestSummarizeAuthLeaseAcquired:
    """Phase 1 verifiability hook (plan-2026-05-03 the fix).

    Operators run ``metaproc pool events --type=auth_lease_acquired
    --summary`` to confirm ROUND_ROBIN actually distributed acquisitions
    across labels — without waiting for the full Phase 2 aggregator.
    """

    def test_per_label_acquisition_counts(self):
        events = [
            _lease_event("alt1"),
            _lease_event("alt1"),
            _lease_event("alt2"),
            _lease_event("alt2"),
            _lease_event("alt2"),
        ]
        report = _summarize_events(events, total_in_file=5, event_type="auth_lease_acquired")
        assert report["matched"] == 5
        assert report["auth_lease_acquired"]["by_label"] == {"alt1": 2, "alt2": 3}

    def test_round_robin_balanced_distribution_visible(self):
        # The shape that, run on a real cohort post-2026-05-03, would
        # show alt1 and alt2 with comparable counts — the inverse of
        # the P0-10 239/0/0 fingerprint.
        events = [_lease_event(lbl) for lbl in ("alt1", "alt2") * 12]
        report = _summarize_events(events, total_in_file=24, event_type="auth_lease_acquired")
        by_label = report["auth_lease_acquired"]["by_label"]
        assert by_label["alt1"] == 12
        assert by_label["alt2"] == 12

    def test_per_policy_counts(self):
        events = [
            _lease_event("alt1", policy="round-robin"),
            _lease_event("alt2", policy="round-robin"),
            _lease_event("alt1", policy="priority-order"),
        ]
        report = _summarize_events(events, total_in_file=3, event_type="auth_lease_acquired")
        by_policy = report["auth_lease_acquired"]["by_policy"]
        assert by_policy["round-robin"] == 2
        assert by_policy["priority-order"] == 1

    def test_unset_policy_reports_as_unset(self):
        events = [{"event": "auth_lease_acquired", "label": "alt1"}]
        report = _summarize_events(events, total_in_file=1, event_type="auth_lease_acquired")
        assert report["auth_lease_acquired"]["by_policy"] == {"(unset)": 1}

    def test_per_label_policy_breakdown(self):
        # Mid-flight policy switch (e.g. resume changes --auth-policy).
        # Per-label-policy lets operators see which slice of the run
        # used which policy — nice-to-have for incident investigations.
        events = [
            _lease_event("alt1", policy="round-robin"),
            _lease_event("alt1", policy="round-robin"),
            _lease_event("alt1", policy="priority-order"),
        ]
        report = _summarize_events(events, total_in_file=3, event_type="auth_lease_acquired")
        assert report["auth_lease_acquired"]["by_label_policy"] == {
            "alt1": {"round-robin": 2, "priority-order": 1},
        }

    def test_empty_stream_reports_zero_matched(self):
        report = _summarize_events([], total_in_file=42, event_type="auth_lease_acquired")
        assert report["matched"] == 0
        assert "auth_lease_acquired" not in report
