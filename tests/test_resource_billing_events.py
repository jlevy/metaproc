"""Tests for BillingEvent emission from runpool process_exit (F4, spec §6.5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from metaproc.engine.resource_rollup import build_resource_artifacts
from metaproc.models.resources import BillingEvent, SampleEvent
from metaproc.viz_loader import load_plan_bundle

_PARENT_PROCESS = """\
---
process:
  name: parent
  steps:
    - id: predict
      mode: code
      handler: metaproc.code_steps.scaffold
---

Parent body.
"""


def _write(tmp_path: Path, rel: str, content: str) -> Path:
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


def _make_runpool_log(path: Path, *, with_machine_type: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    process_exit: dict[str, object] = {
        "event": "process_exit",
        "ts": "2026-04-24T12:01:00Z",
        "pid": 1234,
        "external_id": "ex-1",
        "backend": "local",
        "label": "predict/AAPL",
        "exit_code": 0,
        "elapsed_s": 60.0,
        "peak_rss_bytes": 256_000_000,
    }
    if with_machine_type:
        process_exit["machine_type"] = "n2-standard-4"
    lines = [
        json.dumps(
            {
                "event": "pool_start",
                "ts": "2026-04-24T12:00:00Z",
                "pool_id": "pool-1",
                "backend": "local",
                "max_concurrency": 4,
                "initial_concurrency": 1,
            }
        ),
        json.dumps(process_exit),
    ]
    path.write_text("\n".join(lines) + "\n")


def test_runpool_process_exit_emits_sample_event(tmp_path: Path) -> None:
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    bundle = load_plan_bundle(parent)
    run_dir = tmp_path / "runs" / "2026-04-21"
    _make_runpool_log(run_dir / "predict" / ".logs" / "runpool-events.jsonl")

    result = build_resource_artifacts(bundle=bundle, run_dir=run_dir, run_id="run-1")
    samples = [e for e in result.events if isinstance(e, SampleEvent)]
    assert len(samples) == 1
    s = samples[0]
    assert s.metrics.wall_time_s == pytest.approx(60.0)
    assert s.metrics.local_compute_s == pytest.approx(60.0)
    assert s.metrics.rss_bytes_max == 256_000_000
    assert s.taxonomy.resource_path == ["resource", "local", "runpool_process"]
    assert result.document.hierarchy_root.total_metrics.rss_bytes_max == 256_000_000


def test_billing_event_emitted_when_machine_type_known(tmp_path: Path) -> None:
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    bundle = load_plan_bundle(parent)
    run_dir = tmp_path / "runs" / "2026-04-21"
    _make_runpool_log(
        run_dir / "predict" / ".logs" / "runpool-events.jsonl",
        with_machine_type=True,
    )

    result = build_resource_artifacts(bundle=bundle, run_dir=run_dir, run_id="run-1")
    billings = [e for e in result.events if isinstance(e, BillingEvent)]
    assert len(billings) == 1
    b = billings[0]
    # 60s on n2-standard-4 (4 vCPU - 0.5 reserved = 3.5 vCPU, 16 GiB - 1 GiB = 15 GiB).
    # vm_hours = 60/3600 ≈ 0.01667
    assert b.metrics.billable_vm_hours == pytest.approx(60.0 / 3600.0, rel=1e-3)
    assert b.metrics.billable_vcpu_hours == pytest.approx((60.0 / 3600.0) * 3.5, rel=1e-3)
    assert b.taxonomy.resource_path == ["resource", "cloud", "batch"]


def test_no_billing_event_when_machine_type_absent(tmp_path: Path) -> None:
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    bundle = load_plan_bundle(parent)
    run_dir = tmp_path / "runs" / "2026-04-21"
    _make_runpool_log(
        run_dir / "predict" / ".logs" / "runpool-events.jsonl",
        with_machine_type=False,
    )

    result = build_resource_artifacts(bundle=bundle, run_dir=run_dir, run_id="run-1")
    billings = [e for e in result.events if isinstance(e, BillingEvent)]
    assert billings == []
