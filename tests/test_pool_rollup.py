"""Tests for `metaproc pool rollup` cross-step aggregation.

Covers the pure helpers in :mod:`metaproc.commands.pool` —
no Typer / CLI machinery. Builds fixture sub-pool dicts
in the same shape ``_collect_sub_pools`` returns, then drives
``_build_rollup`` to validate the per-step + cross-step rollup.

Also exercises the directory walk in ``_collect_sub_pools`` against
a tmp-path tree with one ``<run>/.state/steps/<step_id>/runpool-status.yaml``
and matching ``<run>/.logs/runpool/steps/<step_id>/events.jsonl`` per step.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from metaproc.commands.pool import _build_rollup, _collect_sub_pools, _render_rollup
from metaproc.io import to_yaml_string
from metaproc.paths import run_state_dir, runpool_events, runpool_step_events, step_state_dir


def _write_status(run_dir: Path, step_id: str, **fields):
    state = step_state_dir(run_dir, step_id)
    state.mkdir(parents=True, exist_ok=True)
    base = {
        "pool_id": fields.get("pool_id", f"pool-{step_id}"),
        "pid": 1,
        "started_at": "2026-04-27T17:00:00+00:00",
        "updated_at": "2026-04-27T17:30:00+00:00",
        "backend": "local",
        "max_concurrency": fields.get("max_concurrency", 25),
        "current_concurrency": fields.get("current_concurrency", 4),
        "active_count": fields.get("active_count", 0),
        "pending_count": 0,
        "completed_count": fields.get("completed_count", 0),
        "failed_count": fields.get("failed_count", 0),
        "killed_count": fields.get("killed_count", 0),
        "pending_retries": fields.get("pending_retries", 0),
        "pressure": {
            "level": "normal",
            "available_pct": 70.0,
            "swap_used_gb": 0.0,
            "total_memory_gb": 32.0,
            "source": "test",
        },
        "failure_counts": {
            "rate_limited": 0,
            "server_error": 0,
            "timeout": 0,
            "invalid_output": 0,
            "crash": 0,
            "unknown": 0,
        },
        "controller": {
            "mode": "adaptive",
            "operator_cap": 25,
            "effective_target": 25,
            "memory_ceiling": 25,
            "provider_ceiling": 25,
            "bottleneck": "uncapped",
            "recent_rate_limits": 0,
            "pending_retries": 0,
        },
        "processes": [],
        "recent_completions": [],
    }
    if "concurrency_plan" in fields:
        base["concurrency_plan"] = fields["concurrency_plan"]
    (state / "runpool-status.yaml").write_text(to_yaml_string(base))


def _write_root_status(run_dir: Path, **fields: Any) -> None:
    _write_status(run_dir, "temporary-root", **fields)
    temporary = step_state_dir(run_dir, "temporary-root") / "runpool-status.yaml"
    root = run_state_dir(run_dir) / "runpool-status.yaml"
    root.parent.mkdir(parents=True, exist_ok=True)
    temporary.replace(root)


def _concurrency_plan() -> dict[str, Any]:
    return {
        "schema_id": "metaproc.runpool.concurrency-plan/v1",
        "backend": "local",
        "execution_profile": "codex-gpt55",
        "cli_max_concurrency": 10,
        "batch_size": 20,
        "requested_max_concurrency": 10,
        "profile_max_concurrency_hint": 8,
        "host_max_concurrency": 6,
        "configured_max_concurrency": 8,
        "operator_cap": None,
        "initial_concurrency_override": None,
        "initial_concurrency_estimate": 2,
        "initial_concurrency": 2,
        "min_concurrency": 1,
        "estimated_process_rss_bytes": 4 * 1024 * 1024 * 1024,
        "initial_memory_budget_fraction": 0.25,
        "initial_available_memory_bytes": 32 * 1024 * 1024 * 1024,
        "initial_total_memory_bytes": 64 * 1024 * 1024 * 1024,
        "limiting_factor": "memory_estimate",
    }


def _write_auth_outcomes(run_dir: Path, step_id: str, outcomes: list[dict[str, Any]]):
    events_path = runpool_step_events(run_dir, step_id)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({**o, "event": "auth_outcome"}) for o in outcomes]
    events_path.write_text("\n".join(lines) + "\n")


@pytest.fixture
def two_step_run(tmp_path):
    # mine-adhoc: 6 completed; auth_outcomes: 4×alt1 + 2×alt2
    _write_status(
        tmp_path, "mine-adhoc", completed_count=6, max_concurrency=25, current_concurrency=5
    )
    _write_auth_outcomes(
        tmp_path,
        "mine-adhoc",
        [
            {"label": "alt1", "classification": "ok", "retry_count": 0},
            {"label": "alt1", "classification": "ok", "retry_count": 0},
            {"label": "alt1", "classification": "ok", "retry_count": 0},
            {"label": "alt1", "classification": "ok", "retry_count": 0},
            {"label": "alt2", "classification": "ok", "retry_count": 0},
            {"label": "alt2", "classification": "ok", "retry_count": 0},
        ],
    )
    # predict-ticker: 24 failed; auth_outcomes: 24×alt1 (no rotation since alt1 stayed eligible)
    _write_status(
        tmp_path, "predict-ticker", failed_count=24, max_concurrency=25, current_concurrency=25
    )
    _write_auth_outcomes(
        tmp_path,
        "predict-ticker",
        [{"label": "alt1", "classification": "ok", "retry_count": 0} for _ in range(24)],
    )
    return tmp_path


class TestCollectSubPools:
    def test_walks_tree_and_finds_each_step(self, two_step_run):
        entries = _collect_sub_pools(two_step_run, with_auth_outcomes=False)
        step_dirs = sorted(e["step_dir"] for e in entries)
        assert step_dirs == ["mine-adhoc", "predict-ticker"]

    def test_skips_event_load_when_auth_outcomes_off(self, two_step_run):
        entries = _collect_sub_pools(two_step_run, with_auth_outcomes=False)
        # auth_outcomes list is empty when off, even though the events file exists.
        assert all(e["auth_outcomes"] == [] for e in entries)

    def test_loads_auth_outcomes_when_on(self, two_step_run):
        entries = _collect_sub_pools(two_step_run, with_auth_outcomes=True)
        by_step = {e["step_dir"]: e for e in entries}
        assert len(by_step["mine-adhoc"]["auth_outcomes"]) == 6
        assert len(by_step["predict-ticker"]["auth_outcomes"]) == 24

    def test_empty_dir_returns_empty(self, tmp_path):
        assert _collect_sub_pools(tmp_path, with_auth_outcomes=True) == []

    def test_unparsable_status_yields_error_entry(self, tmp_path):
        # A pool dir whose status YAML is corrupt should not abort the
        # whole walk — surface a per-entry error instead.
        state = step_state_dir(tmp_path, "broken")
        state.mkdir(parents=True)
        (state / "runpool-status.yaml").write_text("this is not valid yaml: ][")
        entries = _collect_sub_pools(tmp_path, with_auth_outcomes=False)
        assert len(entries) == 1
        assert entries[0]["status"] is None
        assert "error" in entries[0]

    def test_includes_run_owned_root_pool_and_events(self, tmp_path: Path) -> None:
        _write_root_status(tmp_path, completed_count=1)
        root_events = runpool_events(tmp_path)
        root_events.parent.mkdir(parents=True, exist_ok=True)
        root_events.write_text(
            json.dumps(
                {
                    "event": "auth_outcome",
                    "label": "ambient",
                    "classification": "ok",
                    "retry_count": 0,
                }
            )
            + "\n"
        )

        entries = _collect_sub_pools(tmp_path, with_auth_outcomes=True)

        assert [entry["step_dir"] for entry in entries] == ["."]
        assert entries[0]["status"]["completed_count"] == 1
        assert len(entries[0]["auth_outcomes"]) == 1


class TestBuildRollup:
    def test_per_step_summary_carries_status_fields(self, two_step_run):
        sub_pools = _collect_sub_pools(two_step_run, with_auth_outcomes=False)
        rollup = _build_rollup(sub_pools, with_auth_outcomes=False)
        assert rollup["sub_pool_count"] == 2
        per_step_by_dir = {s["step_dir"]: s for s in rollup["per_step"]}
        assert per_step_by_dir["mine-adhoc"]["completed_count"] == 6
        assert per_step_by_dir["predict-ticker"]["failed_count"] == 24

    def test_totals_sum_across_steps(self, two_step_run):
        sub_pools = _collect_sub_pools(two_step_run, with_auth_outcomes=False)
        rollup = _build_rollup(sub_pools, with_auth_outcomes=False)
        assert rollup["totals"]["completed"] == 6
        assert rollup["totals"]["failed"] == 24

    def test_per_step_summary_carries_concurrency_plan(self, tmp_path):
        _write_status(
            tmp_path,
            "mine-adhoc",
            completed_count=6,
            max_concurrency=8,
            current_concurrency=2,
            concurrency_plan=_concurrency_plan(),
        )

        sub_pools = _collect_sub_pools(tmp_path, with_auth_outcomes=False)
        rollup = _build_rollup(sub_pools, with_auth_outcomes=False)

        step = rollup["per_step"][0]
        assert step["concurrency_plan"]["limiting_factor"] == "memory_estimate"
        assert step["concurrency_plan"]["execution_profile"] == "codex-gpt55"

    def test_render_rollup_includes_concurrency_plan_summary(self, tmp_path):
        _write_status(
            tmp_path,
            "mine-adhoc",
            completed_count=6,
            max_concurrency=8,
            current_concurrency=2,
            concurrency_plan=_concurrency_plan(),
        )
        sub_pools = _collect_sub_pools(tmp_path, with_auth_outcomes=False)
        rollup = _build_rollup(sub_pools, with_auth_outcomes=False)
        rendered: list[str] = []

        class Out:
            def data(self, text: str) -> None:
                rendered.append(text)

        _render_rollup(rollup, out=Out(), with_auth_outcomes=False)

        output = "\n".join(rendered)
        assert "plan: profile=codex-gpt55 backend=local requested=10 configured=8" in output
        assert "limit=memory_estimate host=6 rss=4096MB budget=0.25" in output

    def test_auth_outcome_rollup_aggregates_cross_step(self, two_step_run):
        sub_pools = _collect_sub_pools(two_step_run, with_auth_outcomes=True)
        rollup = _build_rollup(sub_pools, with_auth_outcomes=True)
        ao = rollup["auth_outcomes"]
        # 6 mine-adhoc events + 24 predict-ticker events = 30 cross-step.
        assert ao["total"] == 30
        # alt1 = 4 mine-adhoc + 24 predict-ticker = 28; alt2 = 2 mine-adhoc.
        assert ao["by_label"] == {"alt1": 28, "alt2": 2}

    def test_by_step_label_breakdown(self, two_step_run):
        sub_pools = _collect_sub_pools(two_step_run, with_auth_outcomes=True)
        rollup = _build_rollup(sub_pools, with_auth_outcomes=True)
        by_step = rollup["auth_outcomes"]["by_step_label"]
        assert by_step["mine-adhoc"] == {"alt1": 4, "alt2": 2}
        assert by_step["predict-ticker"] == {"alt1": 24}

    def test_no_auth_outcomes_when_disabled(self, two_step_run):
        sub_pools = _collect_sub_pools(two_step_run, with_auth_outcomes=False)
        rollup = _build_rollup(sub_pools, with_auth_outcomes=False)
        assert "auth_outcomes" not in rollup
        # And the per-step entries don't carry the auth_outcome_count column.
        assert "auth_outcome_count" not in rollup["per_step"][0]

    def test_step_with_error_renders_safely(self, tmp_path):
        # A failed-to-read sub-pool entry must not crash the rollup —
        # the error string should ride through to the per-step list.
        sub_pools = [
            {"step_dir": "predict/broken", "status": None, "error": "oops", "auth_outcomes": []},
        ]
        rollup = _build_rollup(sub_pools, with_auth_outcomes=False)
        assert rollup["per_step"][0]["error"] == "oops"
        assert rollup["totals"]["completed"] == 0
