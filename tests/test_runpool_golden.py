"""Golden tests for runpool event logs and status files.

Captures deterministic snapshots of RunPool behavior via MockBackend.
Unstable fields (timestamps, PIDs, UUIDs, pool_id) are normalized.

Run with --update-golden to regenerate golden files after intentional changes.
"""
# pyright: reportMissingTypeArgument=false

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml

from metaproc.paths import RUNPOOL_EVENTS_FILE
from metaproc.runpool.backend import PreparedLaunch
from metaproc.runpool.mock_backend import MockBackend, MockBehavior
from metaproc.runpool.pool import ProcessConfig, RunPool, RunPoolConfig

_GOLDEN_DIR = Path(__file__).resolve().parent / "golden" / "runpool"


# ── Normalizers ─────────────────────────────────────────────────


def _normalize_concurrency_plan(plan: dict) -> dict:
    """Normalize host-dependent concurrency-plan fields."""
    normalized = dict(plan)
    for key in (
        "initial_available_memory_bytes",
        "initial_total_memory_bytes",
        "initial_concurrency_estimate",
        "initial_concurrency",
    ):
        if key in normalized:
            normalized[key] = 0
    if "limiting_factor" in normalized:
        normalized["limiting_factor"] = "normalized"
    return normalized


def _normalize_event(event: dict) -> dict:
    """Remove or normalize unstable fields from a single event."""
    normalized = dict(event)
    # Remove timestamps
    for key in ("ts", "started_at", "ended_at", "updated_at"):
        normalized.pop(key, None)
    # Normalize pool_id
    if "pool_id" in normalized:
        normalized["pool_id"] = "pool-NORMALIZED"
    # Auto initial concurrency depends on host memory. Dedicated unit tests
    # assert the live pool_start value; golden snapshots keep this stable.
    if normalized.get("event") == "pool_start" and "initial_concurrency" in normalized:
        normalized["initial_concurrency"] = 0
    if normalized.get("event") == "pool_start" and isinstance(
        normalized.get("concurrency_plan"), dict
    ):
        normalized["concurrency_plan"] = _normalize_concurrency_plan(normalized["concurrency_plan"])
    # Normalize PID
    if "pid" in normalized:
        normalized["pid"] = 0
    # Normalize elapsed_s to fixed precision
    if "elapsed_s" in normalized:
        normalized["elapsed_s"] = 0.0
    # Normalize pressure check values (system-dependent)
    if "available_pct" in normalized:
        normalized["available_pct"] = 0.0
    if normalized.get("event") == "pressure_check" and "level" in normalized:
        normalized["level"] = "normalized"
        for key in (
            "swap_used_gb",
            "total_memory_gb",
            "memory_level",
            "swap_delta_gb_per_min",
            "swap_level",
            "disk_free_gb",
            "disk_total_gb",
            "disk_used_pct",
            "disk_level",
            "disk_pressure_cause",
            "source",
            "current_concurrency",
            "active_count",
            "pending_count",
            "memory_ceiling",
            "provider_ceiling",
            "operator_cap",
            "effective_target",
            "bottleneck",
            "active_rss_bytes",
            "active_peak_rss_bytes",
            "active_log_bytes",
        ):
            normalized.pop(key, None)
    # Normalize health metrics that vary by timing
    for key in ("rss_bytes", "peak_rss_bytes", "descendants", "peak_descendants", "log_bytes"):
        if key in normalized:
            normalized[key] = 0
    return normalized


def _normalize_events(events_text: str) -> list[dict]:
    """Parse JSONL, normalize, and sort events for deterministic comparison.

    Events are grouped by type and sorted by label within each group.
    Pool-level events (pool_start, pool_shutdown) keep their natural order.
    Pressure check events are deduplicated (count may vary).
    """
    events = [json.loads(line) for line in events_text.strip().split("\n") if line.strip()]
    normalized = [_normalize_event(e) for e in events]

    # Separate event types
    pool_start = [e for e in normalized if e.get("event") == "pool_start"]
    pool_shutdown = [e for e in normalized if e.get("event") == "pool_shutdown"]
    pressure = [e for e in normalized if e.get("event") == "pressure_check"]
    process_events = [
        e
        for e in normalized
        if e.get("event") not in ("pool_start", "pool_shutdown", "pressure_check")
    ]

    # Group process events by type, sort by label within each group
    by_type: dict[str, list[dict]] = {}
    for e in process_events:
        by_type.setdefault(e.get("event", ""), []).append(e)
    for events_list in by_type.values():
        events_list.sort(key=lambda e: e.get("label", ""))

    # Reconstruct: pool_start, process events sorted by type, pressure (deduped), pool_shutdown
    result = pool_start
    for event_type in sorted(by_type.keys()):
        result.extend(by_type[event_type])
    # Keep exactly one pressure check (count varies by timing)
    if pressure:
        result.append(pressure[0])
    result.extend(pool_shutdown)
    return result


def _normalize_status(status: dict) -> dict:
    """Normalize unstable fields in status YAML."""
    normalized = dict(status)
    normalized.pop("started_at", None)
    normalized.pop("updated_at", None)
    if "pool_id" in normalized:
        normalized["pool_id"] = "pool-NORMALIZED"
    if "pid" in normalized:
        normalized["pid"] = 0
    # current_concurrency is timing-dependent (snapshot at shutdown time)
    if "current_concurrency" in normalized:
        normalized["current_concurrency"] = 0
    # Normalize pressure snapshot (platform-dependent fields)
    if "pressure" in normalized and isinstance(normalized["pressure"], dict):
        normalized["pressure"]["available_pct"] = 0.0
        normalized["pressure"]["source"] = "normalized"
        normalized["pressure"]["swap_used_gb"] = 0.0
        normalized["pressure"]["total_memory_gb"] = 0.0
        normalized["pressure"]["swap_delta_gb_per_min"] = 0.0
        normalized["pressure"]["swap_level"] = "normalized"
        normalized["pressure"]["disk_free_gb"] = 0.0
        normalized["pressure"]["disk_total_gb"] = 0.0
        normalized["pressure"]["disk_used_pct"] = 0.0
        normalized["pressure"]["disk_level"] = "normalized"
        normalized["pressure"]["disk_pressure_cause"] = "normalized"
        normalized["pressure"]["level"] = "normalized"
    # Normalize adaptive controller state — memory_ceiling / provider_ceiling
    # / effective_target / bottleneck depend on the host's free RAM, so CI
    # and dev machines diverge. Goldens should only capture deterministic
    # fields (mode, config) and leave live ceilings platform-independent.
    if "controller" in normalized and isinstance(normalized["controller"], dict):
        for key in ("memory_ceiling", "provider_ceiling", "effective_target"):
            if key in normalized["controller"]:
                normalized["controller"][key] = 0
        if "bottleneck" in normalized["controller"]:
            normalized["controller"]["bottleneck"] = "normalized"
    if "concurrency_plan" in normalized and isinstance(normalized["concurrency_plan"], dict):
        normalized["concurrency_plan"] = _normalize_concurrency_plan(normalized["concurrency_plan"])
    # Normalize processes and recent_completions
    for key in ("processes", "recent_completions"):
        if key in normalized and isinstance(normalized[key], list):
            for proc in normalized[key]:
                proc.pop("started_at", None)
                proc.pop("ended_at", None)
                if "pid" in proc:
                    proc["pid"] = 0
                if "elapsed_s" in proc:
                    proc["elapsed_s"] = 0.0
                # Health metrics vary by timing (health monitor may or may not run)
                if "rss_bytes" in proc:
                    proc["rss_bytes"] = 0
                if "descendants" in proc:
                    proc["descendants"] = 0
                if "log_bytes" in proc:
                    proc["log_bytes"] = 0
                if "peak_rss_bytes" in proc:
                    proc["peak_rss_bytes"] = 0
                if "peak_descendants" in proc:
                    proc["peak_descendants"] = 0
            # Sort by label for deterministic ordering
            normalized[key] = sorted(normalized[key], key=lambda p: p.get("label", ""))
    return normalized


# ── Golden file I/O ──────────────────────────────────────────────


def _read_golden(name: str) -> dict | None:
    path = _GOLDEN_DIR / f"{name}.yaml"
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text())


def _write_golden(name: str, data: dict) -> None:
    _GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    path = _GOLDEN_DIR / f"{name}.yaml"
    path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=True))


# ── Scenario runners ────────────────────────────────────────────


def _make_configs(labels: list[str]) -> list[ProcessConfig]:
    return [
        ProcessConfig(
            label=label,
            launch=PreparedLaunch(command=("echo", label)),
        )
        for label in labels
    ]


_TECH_MIX_15 = [
    "AAPL",
    "ALB",
    "AS",
    "CMG",
    "DOCN",
    "GOOGL",
    "GTLB",
    "MDB",
    "META",
    "MSFT",
    "NVDA",
    "OKTA",
    "PATH",
    "UNH",
    "XOM",
]


def _run_scenario(
    tmp_path: Path,
    backend: MockBackend,
    configs: list[ProcessConfig],
    max_concurrency: int = 15,
) -> dict:
    """Run a scenario and return normalized events + status."""
    config = RunPoolConfig(
        max_concurrency=max_concurrency,
        monitor_interval_s=0.05,
        # The session fixture collapses pressure polling to 50 ms. These snapshots
        # cover deterministic process lifecycle output; adaptive-controller behavior
        # has dedicated tests and must not depend on whether this mock batch happens
        # to finish before a pressure tick on the current host.
        pressure_check_interval_s=60.0,
        state_dir=tmp_path / "state",
        logs_dir=tmp_path / "logs",
    )
    pool = RunPool(config, backend=backend)

    async def run():
        results = await pool.submit_batch(configs)
        await pool.shutdown()
        return results

    results = asyncio.run(run())

    events_file = tmp_path / "logs" / RUNPOOL_EVENTS_FILE
    status_file = tmp_path / "state" / "runpool-status.yaml"

    events = _normalize_events(events_file.read_text()) if events_file.exists() else []
    status = (
        _normalize_status(yaml.safe_load(status_file.read_text())) if status_file.exists() else {}
    )

    return {
        "events": events,
        "status": status,
        "result_summary": {
            "total": len(results),
            "succeeded": sum(1 for r in results if r.exit_code == 0),
            "failed": sum(1 for r in results if r.exit_code != 0),
        },
    }


# ── Test scenarios ───────────────────────────────────────────────


class TestRunPoolGolden:
    """Golden tests for runpool event log and status file snapshots."""

    def test_golden_15_item_success(self, tmp_path, request):
        """15-item local run — all succeed."""
        backend = MockBackend(default_behavior=MockBehavior(exit_code=0, run_duration_s=0.0))
        configs = _make_configs(_TECH_MIX_15)
        actual = _run_scenario(tmp_path, backend, configs)

        name = "15_item_success"
        if request.config.getoption("--update-golden", default=False):
            _write_golden(name, actual)
            pytest.skip("golden file updated")

        golden = _read_golden(name)
        if golden is None:
            _write_golden(name, actual)
            pytest.skip(f"golden file created: {_GOLDEN_DIR / name}.yaml")

        assert actual == golden, (
            f"Runpool output differs from golden file '{name}'. "
            f"Run with --update-golden to accept changes."
        )

    def test_golden_15_item_mock_cloud(self, tmp_path, request):
        """15-item mock-cloud run with external_id fields."""
        backend = MockBackend(default_behavior=MockBehavior(exit_code=0, run_duration_s=0.0))
        configs = _make_configs(_TECH_MIX_15)
        actual = _run_scenario(tmp_path, backend, configs)

        # Verify cloud metadata in events
        start_events = [e for e in actual["events"] if e.get("event") == "process_start"]
        assert all(e.get("external_id", "").startswith("mock-") for e in start_events)

        name = "15_item_mock_cloud"
        if request.config.getoption("--update-golden", default=False):
            _write_golden(name, actual)
            pytest.skip("golden file updated")

        golden = _read_golden(name)
        if golden is None:
            _write_golden(name, actual)
            pytest.skip(f"golden file created: {_GOLDEN_DIR / name}.yaml")

        assert actual == golden, (
            f"Runpool output differs from golden file '{name}'. "
            f"Run with --update-golden to accept changes."
        )

    def test_golden_retry_with_failures(self, tmp_path, request):
        """3 items fail once then succeed on retry (simulated via mixed exit codes)."""
        backend = MockBackend(
            behaviors={
                "OKTA": MockBehavior(exit_code=1, run_duration_s=0.0),
                "ALB": MockBehavior(exit_code=1, run_duration_s=0.0),
                "PATH": MockBehavior(exit_code=1, run_duration_s=0.0),
            },
            default_behavior=MockBehavior(exit_code=0, run_duration_s=0.0),
        )
        configs = _make_configs(_TECH_MIX_15)
        actual = _run_scenario(tmp_path, backend, configs)

        assert actual["result_summary"]["failed"] == 3
        assert actual["result_summary"]["succeeded"] == 12

        name = "retry_with_failures"
        if request.config.getoption("--update-golden", default=False):
            _write_golden(name, actual)
            pytest.skip("golden file updated")

        golden = _read_golden(name)
        if golden is None:
            _write_golden(name, actual)
            pytest.skip(f"golden file created: {_GOLDEN_DIR / name}.yaml")

        assert actual == golden, (
            f"Runpool output differs from golden file '{name}'. "
            f"Run with --update-golden to accept changes."
        )

    def test_golden_preemption_simulation(self, tmp_path, request):
        """Items return preemption exit code (137 = SIGKILL)."""
        backend = MockBackend(
            behaviors={
                "NVDA": MockBehavior(exit_code=137, run_duration_s=0.0),
                "META": MockBehavior(exit_code=137, run_duration_s=0.0),
            },
            default_behavior=MockBehavior(exit_code=0, run_duration_s=0.0),
        )
        configs = _make_configs(_TECH_MIX_15)
        actual = _run_scenario(tmp_path, backend, configs)

        assert actual["result_summary"]["failed"] == 2

        name = "preemption_simulation"
        if request.config.getoption("--update-golden", default=False):
            _write_golden(name, actual)
            pytest.skip("golden file updated")

        golden = _read_golden(name)
        if golden is None:
            _write_golden(name, actual)
            pytest.skip(f"golden file created: {_GOLDEN_DIR / name}.yaml")

        assert actual == golden, (
            f"Runpool output differs from golden file '{name}'. "
            f"Run with --update-golden to accept changes."
        )
