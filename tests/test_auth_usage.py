"""Tests for the auth_usage aggregator (internal-reference).

Spec: docs/project/specs/active/plan-2026-05-03-auth-observability-and-load-balancing.md
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from metaproc.dispatch.auth_usage import (
    LabelUsage,
    PoolStateLiteral,
    RateLimitWindow,
    aggregate_label_usage_for_run,
    attribute_session_to_label,
    detect_inconsistencies,
    iter_runpool_events,
)
from metaproc.dispatch.credential_pool import (
    EntryState,
    EntryStatus,
    PoolEntry,
    Vehicle,
)
from metaproc.paths import runpool_step_events

# ── Helpers / fakes ─────────────────────────────────────────────


@dataclass
class _InMemoryPool:
    """Minimal :class:`PoolBackend` implementation for tests.

    Mirrors the in-memory pool fakes in test_credential_pool /
    test_pool_dispatch — duplicated to keep this module's tests
    self-contained.
    """

    entries: dict[tuple[str, str], PoolEntry] = field(default_factory=dict)

    def list_entries(self, adapter: str | None = None) -> list[PoolEntry]:
        return [e for (a, _l), e in self.entries.items() if adapter is None or a == adapter]

    def get_entry(self, adapter: str, label: str) -> PoolEntry:
        return self.entries[(adapter, label)]

    # Unused by aggregator but required by PoolBackend Protocol consumers
    # in some fixtures. Stubs to keep type-checkers happy.
    def upsert_entry(
        self, adapter: str, label: str, *, blob: str | None, state: Any, expected_etag: Any = None
    ) -> str:
        self.entries[(adapter, label)] = PoolEntry(
            adapter=adapter, label=label, blob=blob or "", state=state, etag="et-1"
        )
        return "et-1"

    def delete_entry(self, adapter: str, label: str) -> None:
        self.entries.pop((adapter, label), None)


def _seed_active(
    pool: _InMemoryPool,
    adapter: str,
    label: str,
    *,
    last_ok_ts: int | None = None,
    cooling_until_ts: int | None = None,
    status: str = "active",
    vehicle: Vehicle = Vehicle.OAUTH_TOKEN,
) -> None:
    pool.entries[(adapter, label)] = PoolEntry(
        adapter=adapter,
        label=label,
        blob="blob",
        state=EntryState(
            status=cast(EntryStatus, status),
            fp=f"fp-{label}",
            last_ok_ts=last_ok_ts,
            cooling_until_ts=cooling_until_ts,
            vehicle=vehicle,
        ),
        etag="e1",
    )


def _write_runpool_events(run_dir: Path, step: str, events: list[dict[str, Any]]) -> Path:
    """Write a list of events to the V2 step runpool event stream."""
    path = runpool_step_events(run_dir, step)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")
    return path


def _outcome(
    *,
    label: str,
    classification: str = "ok",
    ts: str = "2026-05-04T12:00:00+00:00",
    run_id: str = "run-x",
    item: str = "AAPL",
    attempt: int = 0,
    session_log_path: str = "",
    known_bug: str = "",
    schema_version: int = 2,
) -> dict[str, Any]:
    return {
        "event": "auth_outcome",
        "schema_version": schema_version,
        "ts": ts,
        "adapter": "claude-code-cli",
        "label": label,
        "classification": classification,
        "run_id": run_id,
        "step_id": "predict-ticker",
        "item": item,
        "attempt": attempt,
        "session_log_path": session_log_path,
        "known_bug_signature": known_bug,
    }


def _lease(
    *,
    label: str,
    ts: str = "2026-05-04T11:59:00+00:00",
    run_id: str = "run-x",
    item: str = "AAPL",
    attempt: int = 0,
    session_log_path: str = "/tmp/session.jsonl",
    policy: str = "round-robin",
) -> dict[str, Any]:
    return {
        "event": "auth_lease_acquired",
        "schema_version": 2,
        "ts": ts,
        "adapter": "claude-code-cli",
        "label": label,
        "slot_dir": "/tmp/slot",
        "run_id": run_id,
        "step_id": "predict-ticker",
        "item": item,
        "attempt": attempt,
        "session_log_path": session_log_path,
        "policy": policy,
    }


def _rate_limit_record(
    *,
    kind: str,
    status: str = "allowed",
    utilization: float | None = 0.5,
    resets_at: int | None = 1_770_000_000,
    ts: str = "2026-05-04T12:30:00+00:00",
) -> dict[str, Any]:
    info: dict[str, Any] = {
        "rateLimitType": kind,
        "status": status,
    }
    if utilization is not None:
        info["utilization"] = utilization
    if resets_at is not None:
        info["resetsAt"] = resets_at
    return {
        "type": "rate_limit_event",
        "ts": ts,
        "rate_limit_info": info,
    }


def _write_session_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


# ── iter_runpool_events ─────────────────────────────────────────


class TestIterRunpoolEvents:
    def test_missing_dir_yields_nothing(self, tmp_path: Path):
        assert list(iter_runpool_events(tmp_path / "nonexistent")) == []

    def test_walks_per_step_jsonls_in_sorted_order(self, tmp_path: Path):
        _write_runpool_events(tmp_path, "step-a", [{"event": "a", "n": 1}])
        _write_runpool_events(tmp_path, "step-b", [{"event": "b", "n": 2}])
        events = list(iter_runpool_events(tmp_path))
        # Sorted by file path => step-a/.../jsonl before step-b/.../jsonl.
        assert [e["event"] for e in events] == ["a", "b"]

    def test_skips_malformed_lines(self, tmp_path: Path):
        path = runpool_step_events(tmp_path, "step")
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps({"event": "ok", "n": 1})
            + "\n"
            + "not-json\n"
            + json.dumps({"event": "ok", "n": 2})
            + "\n"
        )
        events = list(iter_runpool_events(tmp_path))
        assert [e["n"] for e in events] == [1, 2]


# ── attribute_session_to_label ──────────────────────────────────


class TestAttributeSessionToLabel:
    def test_primary_key_match_via_lease_event(self):
        session = "/tmp/predict_AAPL.jsonl"
        events = [
            _lease(label="alt2", session_log_path=session),
            _outcome(label="alt2", session_log_path=session),
        ]
        attribution = attribute_session_to_label(Path(session), runpool_events=events)
        assert attribution is not None
        assert attribution.adapter == "claude-code-cli"
        assert attribution.label == "alt2"
        assert attribution.via == "primary_key"
        assert attribution.lease_event is not None
        assert attribution.outcome_event is not None

    def test_primary_key_match_via_outcome_only(self):
        # Schema-v2 outcome present but no matching lease event (e.g.
        # the lease emission failed). Outcome's join keys are still
        # authoritative.
        session = "/tmp/predict_NVDA.jsonl"
        events = [_outcome(label="alt1", session_log_path=session)]
        attribution = attribute_session_to_label(Path(session), runpool_events=events)
        assert attribution is not None
        assert attribution.label == "alt1"
        assert attribution.via == "primary_key"
        assert attribution.lease_event is None

    def test_unknown_when_no_join_match(self):
        # Schema-v1 only events: no session_log_path field, no match.
        events = [
            {
                "event": "auth_outcome",
                "label": "alt1",
                "classification": "ok",
                "ts": "2026-05-04T12:00:00+00:00",
            }
        ]
        attribution = attribute_session_to_label(
            Path("/tmp/never-leased.jsonl"), runpool_events=events
        )
        assert attribution is not None
        assert attribution.via == "unknown"
        assert attribution.label == ""

    def test_returns_none_when_no_event_source(self):
        # Caller passed neither runpool_events nor a path to read.
        assert attribute_session_to_label(Path("/tmp/x.jsonl"), runpool_events_path=None) is None


# ── aggregate_label_usage_for_run ───────────────────────────────


class TestAggregateLabelUsageForRun:
    def test_empty_run_returns_empty(self, tmp_path: Path):
        # No events, no pool — empty result.
        assert aggregate_label_usage_for_run(tmp_path) == {}

    def test_pool_only_no_events_surfaces_state(self, tmp_path: Path):
        pool = _InMemoryPool()
        _seed_active(pool, "claude-code-cli", "alt1", last_ok_ts=1_700_000_000)
        usage = aggregate_label_usage_for_run(tmp_path, backend=pool)
        assert ("claude-code-cli", "alt1") in usage
        u = usage[("claude-code-cli", "alt1")]
        assert u.pool_state == "active"
        assert u.invocations_total == 0
        assert u.fingerprint == "fp-alt1"
        assert u.last_ok_ts is not None

    def test_outcome_aggregation_per_label(self, tmp_path: Path):
        # 3 alt1 outcomes (2 ok, 1 cooling) + 1 alt2 outcome (ok).
        events = [
            _outcome(label="alt1", classification="ok"),
            _outcome(label="alt1", classification="ok"),
            _outcome(label="alt1", classification="cooling"),
            _outcome(label="alt2", classification="ok"),
        ]
        _write_runpool_events(tmp_path, "predict-ticker", events)
        usage = aggregate_label_usage_for_run(tmp_path)
        alt1 = usage[("claude-code-cli", "alt1")]
        alt2 = usage[("claude-code-cli", "alt2")]
        assert alt1.invocations_total == 3
        assert alt1.invocations_ok == 2
        assert alt1.invocations_failed == 1
        assert alt2.invocations_total == 1
        assert alt2.invocations_ok == 1

    def test_known_bugs_counted_per_label(self, tmp_path: Path):
        events = [
            _outcome(
                label="alt1", classification="unknown", known_bug="claude-startup-exit-1-silent"
            ),
            _outcome(
                label="alt1", classification="unknown", known_bug="claude-startup-exit-1-silent"
            ),
            _outcome(label="alt1", classification="unknown", known_bug="other-bug"),
        ]
        _write_runpool_events(tmp_path, "step", events)
        usage = aggregate_label_usage_for_run(tmp_path)
        alt1 = usage[("claude-code-cli", "alt1")]
        assert alt1.known_bugs == {
            "claude-startup-exit-1-silent": 2,
            "other-bug": 1,
        }

    def test_last_used_ts_tracks_newest_outcome(self, tmp_path: Path):
        events = [
            _outcome(label="alt1", ts="2026-05-04T08:00:00+00:00"),
            _outcome(label="alt1", ts="2026-05-04T17:14:00+00:00"),
            _outcome(label="alt1", ts="2026-05-04T11:00:00+00:00"),
        ]
        _write_runpool_events(tmp_path, "step", events)
        usage = aggregate_label_usage_for_run(tmp_path)
        alt1 = usage[("claude-code-cli", "alt1")]
        assert alt1.last_used_ts is not None
        assert alt1.last_used_ts.hour == 17

    def test_rate_limit_attribution_via_session_log_path(self, tmp_path: Path):
        # Lease event records the session_log_path; aggregator opens
        # that JSONL and pulls the most-recent rate_limit_event per
        # window, attributing the result to the lease's label.
        session_log = tmp_path / "session-alt2.jsonl"
        _write_session_jsonl(
            session_log,
            [
                _rate_limit_record(
                    kind="five_hour", status="rejected", utilization=1.0, resets_at=1_770_000_000
                ),
                _rate_limit_record(kind="seven_day", status="allowed_warning", utilization=0.94),
            ],
        )
        events = [
            _lease(label="alt2", session_log_path=str(session_log)),
            _outcome(label="alt2", session_log_path=str(session_log)),
        ]
        _write_runpool_events(tmp_path, "step", events)
        usage = aggregate_label_usage_for_run(tmp_path)
        alt2 = usage[("claude-code-cli", "alt2")]
        assert alt2.five_hour is not None
        assert alt2.five_hour.status == "rejected"
        assert alt2.five_hour.utilization == 1.0
        assert alt2.five_hour.resets_at == 1_770_000_000
        assert alt2.seven_day is not None
        assert alt2.seven_day.utilization == 0.94

    def test_per_window_ts_picks_newest_record(self, tmp_path: Path):
        # internal review (P2): older session's high-utilization 5h
        # was previously able to clobber a newer session's low-
        # utilization 5h depending on set-iteration order. The fix
        # tracks per-window timestamps; this test pins the behavior.
        # Two sessions for the same label, both with 5h records but
        # with intentionally inverted utilization-vs-time ordering.
        session_old = tmp_path / "session-old.jsonl"
        _write_session_jsonl(
            session_old,
            [
                _rate_limit_record(
                    kind="five_hour",
                    status="rejected",
                    utilization=1.0,
                    resets_at=1_770_000_000,
                    ts="2026-05-04T08:00:00+00:00",  # OLDER ts, higher util
                ),
            ],
        )
        session_new = tmp_path / "session-new.jsonl"
        _write_session_jsonl(
            session_new,
            [
                _rate_limit_record(
                    kind="five_hour",
                    status="allowed",
                    utilization=0.10,
                    resets_at=1_770_000_500,
                    ts="2026-05-04T18:00:00+00:00",  # NEWER ts, lower util
                ),
            ],
        )
        events = [
            _lease(
                label="alt2",
                session_log_path=str(session_old),
                ts="2026-05-04T07:59:00+00:00",
            ),
            _lease(
                label="alt2",
                session_log_path=str(session_new),
                ts="2026-05-04T17:59:00+00:00",
            ),
        ]
        _write_runpool_events(tmp_path, "step", events)
        usage = aggregate_label_usage_for_run(tmp_path)
        alt2 = usage[("claude-code-cli", "alt2")]
        # The newer record (low utilization) must win regardless of
        # which session-set iteration order Python picks.
        assert alt2.five_hour is not None
        assert alt2.five_hour.utilization == 0.10
        assert alt2.five_hour.status == "allowed"

    def test_missing_session_log_doesnt_raise(self, tmp_path: Path):
        events = [_lease(label="alt1", session_log_path="/tmp/never-existed.jsonl")]
        _write_runpool_events(tmp_path, "step", events)
        # Just don't crash; rate-limit windows simply absent.
        usage = aggregate_label_usage_for_run(tmp_path)
        alt1 = usage[("claude-code-cli", "alt1")]
        assert alt1.five_hour is None
        assert alt1.seven_day is None

    def test_pool_state_merges_with_outcome_aggregation(self, tmp_path: Path):
        pool = _InMemoryPool()
        _seed_active(pool, "claude-code-cli", "alt1")
        events = [
            _outcome(label="alt1", classification="ok"),
            _outcome(label="alt1", classification="ok"),
        ]
        _write_runpool_events(tmp_path, "step", events)
        usage = aggregate_label_usage_for_run(tmp_path, backend=pool)
        alt1 = usage[("claude-code-cli", "alt1")]
        assert alt1.pool_state == "active"
        assert alt1.invocations_total == 2

    def test_label_in_pool_but_unused_in_run_surfaces_as_zero_invocations(self, tmp_path: Path):
        # The diagnostic shape that internal-reference's fix prevents: alt2
        # in the pool, never invoked. LabelUsage shape lets doctor
        # flag it.
        pool = _InMemoryPool()
        _seed_active(pool, "claude-code-cli", "alt1")
        _seed_active(pool, "claude-code-cli", "alt2")
        events = [
            _outcome(label="alt1", classification="ok"),
        ]
        _write_runpool_events(tmp_path, "step", events)
        usage = aggregate_label_usage_for_run(tmp_path, backend=pool)
        alt2 = usage[("claude-code-cli", "alt2")]
        assert alt2.invocations_total == 0
        assert alt2.pool_state == "active"

    def test_adapter_filter_excludes_other_adapters(self, tmp_path: Path):
        pool = _InMemoryPool()
        _seed_active(pool, "claude-code-cli", "alt1")
        _seed_active(pool, "codex-cli", "default")
        usage = aggregate_label_usage_for_run(tmp_path, backend=pool, adapter="claude-code-cli")
        assert ("claude-code-cli", "alt1") in usage
        assert ("codex-cli", "default") not in usage

    def test_schema_v1_outcomes_aggregate_without_join_keys(self, tmp_path: Path):
        # Legacy events with no session_log_path. We can't attribute
        # rate_limit windows, but per-label aggregation still works.
        events = [
            _outcome(label="alt1", classification="ok", schema_version=1, session_log_path=""),
            _outcome(label="alt1", classification="ok", schema_version=1, session_log_path=""),
        ]
        _write_runpool_events(tmp_path, "step", events)
        usage = aggregate_label_usage_for_run(tmp_path)
        alt1 = usage[("claude-code-cli", "alt1")]
        assert alt1.invocations_total == 2
        assert alt1.invocations_ok == 2
        # No JSONL attribution → no rate-limit windows.
        assert alt1.five_hour is None

    def test_retries_with_same_item_attempt_plus_one_pair_correctly(self, tmp_path: Path):
        # Same item, two attempts → two outcome events. Each counts
        # independently in the per-label total, distinguished by the
        # (item, attempt) primary key.
        events = [
            _outcome(label="alt1", item="AAPL", attempt=0, classification="cooling"),
            _outcome(label="alt2", item="AAPL", attempt=1, classification="ok"),
        ]
        _write_runpool_events(tmp_path, "step", events)
        usage = aggregate_label_usage_for_run(tmp_path)
        assert usage[("claude-code-cli", "alt1")].invocations_total == 1
        assert usage[("claude-code-cli", "alt1")].invocations_failed == 1
        assert usage[("claude-code-cli", "alt2")].invocations_total == 1
        assert usage[("claude-code-cli", "alt2")].invocations_ok == 1


# ── Cross-source consistency ────────────────────────────────────


def _build_label_usage(
    *,
    pool_state: str = "active",
    invocations_total: int = 0,
    five_hour: RateLimitWindow | None = None,
    seven_day: RateLimitWindow | None = None,
) -> LabelUsage:
    return LabelUsage(
        adapter="claude-code-cli",
        label="alt1",
        vehicle="claude_code_oauth_token",
        fingerprint="fp",
        pool_state=cast(PoolStateLiteral, pool_state),
        cooling_until=None,
        last_ok_ts=None,
        last_quota_ts=None,
        one_hour=None,
        five_hour=five_hour,
        seven_day=seven_day,
        last_event_ts=None,
        invocations_total=invocations_total,
        invocations_ok=invocations_total,
        invocations_failed=0,
        known_bugs={},
        last_used_ts=None,
        last_run_id=None,
        probe_state=None,
        probe_request_id=None,
        probe_age_s=None,
    )


class TestDetectInconsistencies:
    def test_clean_label_yields_no_issues(self):
        usage = _build_label_usage(
            pool_state="active",
            invocations_total=10,
            five_hour=RateLimitWindow(
                kind="five_hour", status="allowed", utilization=0.4, resets_at=1
            ),
        )
        assert detect_inconsistencies(usage) == []

    def test_pool_active_but_jsonl_shows_full_5h_flags(self):
        usage = _build_label_usage(
            pool_state="active",
            invocations_total=5,
            five_hour=RateLimitWindow(
                kind="five_hour", status="rejected", utilization=1.0, resets_at=1
            ),
        )
        issues = detect_inconsistencies(usage)
        assert any("five_hour utilization=1.0" in msg for msg in issues)

    def test_active_label_zero_invocations_flags_load_balance_concern(self):
        usage = _build_label_usage(pool_state="active", invocations_total=0)
        issues = detect_inconsistencies(usage)
        assert any("0 invocations" in msg for msg in issues)

    def test_pool_cooling_with_no_recent_events_flags_stale(self):
        usage = _build_label_usage(
            pool_state="cooling",
            invocations_total=0,
            five_hour=None,
            seven_day=None,
        )
        issues = detect_inconsistencies(usage)
        assert any("cooling" in msg.lower() for msg in issues)
