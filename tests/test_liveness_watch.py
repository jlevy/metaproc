"""Tests for metaproc liveness-watch glob expansion (bead internal-reference).

Covers the --heartbeat-glob / --mtime-glob auto-discovery behavior added to
support multi-lane EIA batches where new lanes launch over time and the
supervising agent's monitor needs to pick them up without restart.

See logbook arb-2026-05-28-thu-ensemble § L10.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import typer

from metaproc.commands.liveness_watch import (
    GlobSpec,
    _parse_heartbeat_glob_arg,
    _parse_mtime_glob_arg,
)


def test_parse_mtime_glob_arg_basic() -> None:
    """--mtime-glob accepts GLOB with optional STALE_SECONDS."""
    spec = _parse_mtime_glob_arg("/tmp/runs/*.log", 600)
    assert spec.pattern == "/tmp/runs/*.log"
    assert spec.field is None
    assert spec.stale_seconds == 600

    spec = _parse_mtime_glob_arg("/tmp/runs/*.log=120", 600)
    assert spec.pattern == "/tmp/runs/*.log"
    assert spec.stale_seconds == 120


def test_parse_heartbeat_glob_arg_basic() -> None:
    """--heartbeat-glob accepts GLOB:FIELD with optional STALE_SECONDS."""
    spec = _parse_heartbeat_glob_arg("/tmp/arb-*/lease.yaml:last_heartbeat_at", 600)
    assert spec.pattern == "/tmp/arb-*/lease.yaml"
    assert spec.field == "last_heartbeat_at"
    assert spec.stale_seconds == 600

    spec = _parse_heartbeat_glob_arg("/tmp/arb-*/lease.yaml:last_heartbeat_at=300", 600)
    assert spec.stale_seconds == 300


def test_parse_heartbeat_glob_arg_rejects_missing_field() -> None:
    """--heartbeat-glob without :FIELD raises a clear error."""
    with pytest.raises(typer.BadParameter):
        _parse_heartbeat_glob_arg("/tmp/arb-*/lease.yaml", 600)


def test_glob_spec_expand_picks_up_new_files(tmp_path: Path) -> None:
    """GlobSpec.expand() returns one Signal per current match — and picks up
    new files on subsequent calls (the auto-discovery property).
    """
    # Start with no matching files.
    spec = GlobSpec(
        label_prefix="hb",
        pattern=str(tmp_path / "arb-*/lease.yaml"),
        field="last_heartbeat_at",
        stale_seconds=300,
    )
    assert spec.expand() == []

    # Add one lane lease file → expand sees it.
    lane1 = tmp_path / "arb-2026-05-28-tier1-lane-a"
    lane1.mkdir()
    (lane1 / "lease.yaml").write_text("last_heartbeat_at: '2026-05-28T20:00:00'\n")
    signals = spec.expand()
    assert len(signals) == 1
    assert "lane-a" in signals[0].label

    # Add a second lane → next expand picks it up too (no restart needed).
    lane2 = tmp_path / "arb-2026-05-28-tier2-lane-b"
    lane2.mkdir()
    (lane2 / "lease.yaml").write_text("last_heartbeat_at: '2026-05-28T20:00:00'\n")
    signals = spec.expand()
    assert len(signals) == 2
    labels = {s.label for s in signals}
    assert any("lane-a" in lbl for lbl in labels)
    assert any("lane-b" in lbl for lbl in labels)


def test_glob_spec_mtime_expand_reads_actual_mtime(tmp_path: Path) -> None:
    """Mtime-flavored GlobSpec returns signals whose getter reads file mtime."""
    spec = GlobSpec(
        label_prefix="mtime",
        pattern=str(tmp_path / "*.log"),
        field=None,
        stale_seconds=600,
    )
    log = tmp_path / "test.log"
    log.write_text("hello\n")

    signals = spec.expand()
    assert len(signals) == 1
    ts = signals[0].getter()
    assert ts is not None
    # Should be near "now" (within last few seconds).
    age_s = datetime.now(tz=UTC).timestamp() - ts
    assert 0 <= age_s < 5


def test_glob_spec_heartbeat_expand_reads_yaml_field(tmp_path: Path) -> None:
    """Heartbeat-flavored GlobSpec returns signals whose getter reads the YAML
    field timestamp.
    """
    spec = GlobSpec(
        label_prefix="hb",
        pattern=str(tmp_path / "lease.yaml"),
        field="last_heartbeat_at",
        stale_seconds=300,
    )
    (tmp_path / "lease.yaml").write_text("last_heartbeat_at: '2026-05-28T20:00:00'\n")
    signals = spec.expand()
    assert len(signals) == 1
    ts = signals[0].getter()
    assert ts is not None
    expected = datetime(2026, 5, 28, 20, 0, 0, tzinfo=UTC).timestamp()
    assert ts == expected
