"""Tests for PsutilSampler (B9, spec §6.4)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from metaproc.logutil.resource_events import ResourceEventLogger, read_events
from metaproc.models.resources import (
    HierarchyRef,
    SampleEvent,
    SourceRef,
)
from metaproc.osutils.psutil_sampler import PsutilSampler


def _hier() -> HierarchyRef:
    return HierarchyRef(run_id="run-1", step_node_id="normalize-items")


def _src() -> SourceRef:
    return SourceRef(kind="psutil_sampler", path=".logs/resource-events.jsonl")


def test_sampler_records_at_least_one_sample_in_short_window() -> None:
    """Sampler emits a final shutdown sample even for very short steps."""
    with PsutilSampler(hierarchy=_hier(), source=_src(), interval_s=0.05) as sampler:
        time.sleep(0.02)
    assert sampler.stats.sample_count >= 1
    assert sampler.stats.rss_bytes_max > 0


def test_sampler_records_multiple_samples_over_time() -> None:
    with PsutilSampler(hierarchy=_hier(), source=_src(), interval_s=0.05) as sampler:
        time.sleep(0.25)
    # At least two samples (a couple of cadence wake-ups + the shutdown sample).
    assert sampler.stats.sample_count >= 2


def test_sampler_writes_sample_events_to_logger(tmp_path: Path) -> None:
    target = tmp_path / "resource-events.jsonl"
    with (
        ResourceEventLogger(target) as logger,
        PsutilSampler(
            hierarchy=_hier(),
            source=_src(),
            logger=logger,
            interval_s=0.05,
        ),
    ):
        time.sleep(0.15)

    parsed = read_events(target)
    assert parsed, "expected at least one SampleEvent to land on disk"
    assert all(isinstance(ev, SampleEvent) for ev in parsed)
    assert all(ev.metrics.rss_bytes_max is not None for ev in parsed)


def test_sampler_rejects_zero_or_negative_interval() -> None:
    with pytest.raises(ValueError):
        PsutilSampler(hierarchy=_hier(), source=_src(), interval_s=0.0)
    with pytest.raises(ValueError):
        PsutilSampler(hierarchy=_hier(), source=_src(), interval_s=-1.0)


def test_sampler_aggregate_stats_consistent() -> None:
    with PsutilSampler(hierarchy=_hier(), source=_src(), interval_s=0.05) as sampler:
        time.sleep(0.1)
    s = sampler.stats
    if s.sample_count >= 1:
        assert s.cpu_pct_max >= s.cpu_pct_avg - 1e-6
        assert s.rss_bytes_max >= int(s.rss_bytes_avg) - 1
        assert len(s.samples) == s.sample_count


def test_sampler_does_not_raise_when_attached_to_self_pid() -> None:
    """Targeting the current process pid is the default; verify it works explicitly."""

    with PsutilSampler(
        hierarchy=_hier(), source=_src(), interval_s=0.05, pid=os.getpid()
    ) as sampler:
        time.sleep(0.05)
    assert sampler.stats.sample_count >= 1
