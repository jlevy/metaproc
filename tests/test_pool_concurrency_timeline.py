"""Tests for `metaproc pool concurrency-timeline` aggregation.

Covers ``_build_concurrency_timeline`` — the pure helper that turns
a stream of pool events into a trajectory (or bucketed view) of the
concurrency cap over time. Driving the helper directly avoids the
Typer / file-IO machinery; the CLI wrapper is a thin surface.
"""

from __future__ import annotations

from metaproc.commands.pool import _build_concurrency_timeline


def _pool_start(ts: str = "2026-04-27T12:00:00+00:00", initial: int = 0, max_c: int = 25):
    return {
        "event": "pool_start",
        "ts": ts,
        "backend": "local",
        "max_concurrency": max_c,
        "initial_concurrency": initial,
    }


def _adjust(ts: str, old: int, new: int, reason: str = "pressure_normal"):
    return {
        "event": "concurrency_adjust",
        "ts": ts,
        "old": old,
        "new": new,
        "reason": reason,
    }


class TestConcurrencyTimelineUnbucketed:
    def test_extracts_pool_start_metadata(self):
        events = [_pool_start()]
        timeline = _build_concurrency_timeline(events, bucket_minutes=0)
        assert timeline["pool_start"]["backend"] == "local"
        assert timeline["pool_start"]["max_concurrency"] == 25

    def test_lists_each_adjustment_with_relative_offset(self):
        events = [
            _pool_start(ts="2026-04-27T12:00:00+00:00"),
            _adjust("2026-04-27T12:00:30+00:00", 4, 5),
            _adjust("2026-04-27T12:05:30+00:00", 5, 10),
            _adjust("2026-04-27T13:30:30+00:00", 10, 25),
        ]
        timeline = _build_concurrency_timeline(events, bucket_minutes=0)
        offsets = [a["offset"] for a in timeline["adjustments"]]
        # First adjustment at +30s, second at +5m30s, third at +1h30m30s.
        assert offsets == ["T+00:00:30", "T+00:05:30", "T+01:30:30"]

    def test_final_is_last_adjustments_new_value(self):
        events = [
            _pool_start(),
            _adjust("2026-04-27T12:00:30+00:00", 4, 5),
            _adjust("2026-04-27T12:01:30+00:00", 5, 25),
        ]
        timeline = _build_concurrency_timeline(events, bucket_minutes=0)
        assert timeline["final_concurrency"] == 25

    def test_final_falls_back_to_initial_when_no_adjustments(self):
        events = [_pool_start(initial=4)]
        timeline = _build_concurrency_timeline(events, bucket_minutes=0)
        assert timeline["final_concurrency"] == 4
        assert timeline["adjustments_total"] == 0

    def test_handles_unparseable_ts_without_crashing(self):
        events = [
            _pool_start(),
            {"event": "concurrency_adjust", "ts": "not-a-date", "old": 4, "new": 5, "reason": "x"},
        ]
        timeline = _build_concurrency_timeline(events, bucket_minutes=0)
        # Bad ts → empty offset, but the adjustment is still listed.
        assert timeline["adjustments"][0]["offset"] == ""
        assert timeline["adjustments"][0]["new"] == 5


class TestConcurrencyTimelineBucketed:
    def test_groups_adjustments_into_buckets(self):
        # Bucket size = 5 min; place adjustments in two buckets.
        events = [
            _pool_start(ts="2026-04-27T12:00:00+00:00"),
            _adjust("2026-04-27T12:00:10+00:00", 4, 5),
            _adjust("2026-04-27T12:01:30+00:00", 5, 6),
            _adjust("2026-04-27T12:06:00+00:00", 6, 10),  # next bucket
        ]
        timeline = _build_concurrency_timeline(events, bucket_minutes=5)
        assert timeline["bucket_minutes"] == 5
        assert len(timeline["buckets"]) == 2

    def test_bucket_min_max_last_track_correctly(self):
        events = [
            _pool_start(ts="2026-04-27T12:00:00+00:00"),
            _adjust("2026-04-27T12:00:10+00:00", 4, 5),
            _adjust("2026-04-27T12:00:30+00:00", 5, 7),
            _adjust("2026-04-27T12:01:00+00:00", 7, 3),
            _adjust("2026-04-27T12:02:00+00:00", 3, 9),
        ]
        timeline = _build_concurrency_timeline(events, bucket_minutes=5)
        b = timeline["buckets"][0]
        assert b["min"] == 3
        assert b["max"] == 9
        assert b["last"] == 9  # last seen ``new`` value in the bucket
        assert b["adjustment_count"] == 4

    def test_bucket_offset_uses_hh_mm_ss_format(self):
        # Ensure the offset reads as wall-clock hours/minutes, not raw
        # cumulative minutes (pre-fix bug surfaced T+285:00:00 for a
        # ~5-hour-old pool).
        events = [
            _pool_start(ts="2026-04-27T12:00:00+00:00"),
            _adjust("2026-04-27T16:30:00+00:00", 4, 5),  # 4h30m later → bucket idx 54 at 5m
        ]
        timeline = _build_concurrency_timeline(events, bucket_minutes=5)
        offset = timeline["buckets"][0]["bucket_start_offset"]
        # 54 * 5 = 270 minutes = 4h 30m. Should render as T+04:30:00.
        assert offset == "T+04:30:00"

    def test_reasons_tallied_per_bucket(self):
        events = [
            _pool_start(ts="2026-04-27T12:00:00+00:00"),
            _adjust("2026-04-27T12:00:10+00:00", 4, 5, reason="pressure_normal"),
            _adjust("2026-04-27T12:00:30+00:00", 5, 6, reason="pressure_normal"),
            _adjust("2026-04-27T12:01:00+00:00", 6, 5, reason="pressure_elevated"),
        ]
        timeline = _build_concurrency_timeline(events, bucket_minutes=5)
        assert timeline["buckets"][0]["reasons"] == {
            "pressure_normal": 2,
            "pressure_elevated": 1,
        }
