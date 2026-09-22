"""Operations summary and rollup folded from a synthetic composite run tree.

Every expected figure below is computed by hand from the fixture timeline, so a change
in the fold shows up as a changed number rather than a changed snapshot.
"""

from __future__ import annotations

import gzip
import json
import logging
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError
from softschema import (
    ArtifactValidationResult,
    Contract,
    SchemaStatus,
    compile_model,
    validate_artifact,
)
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.commands.run_process import _write_run_config
from metaproc.engine import operations_summary as ops
from metaproc.engine.operations_render import render_rollup_markdown, render_summary_markdown
from metaproc.engine.operations_rollup import build_operations_rollup
from metaproc.engine.resource_finalization import (
    finalize_run_resources,
    resource_artifacts_need_recovery,
)
from metaproc.io import fmf_read_frontmatter, fmf_write, to_yaml_string
from metaproc.io.markdown_table import render_markdown_table
from metaproc.models.operations_summary import (
    OPERATIONS_SUMMARY_CONTRACT,
    AgentOperationsSummary,
    PoolRow,
    RunFigures,
)
from metaproc.models.resource_budget import FinalizationState
from metaproc.models.resource_summary import (
    RESOURCE_USAGE_SUMMARY_CONTRACT,
    ResourceUsageSummary,
)
from metaproc.plugins.discovery import get_plugin_registry

runner = CliRunner()
_ROOT_PROCESS = "cohort"
_ITEM_PROCESS = "author-ticker"
_DEPTH_PROCESS = "depth-ticker"


# ── Fixture builders ──────────────────────────────────────────────


def _yaml(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_yaml_string(data), encoding="utf-8")


def _ts(clock: str) -> str:
    return f"2026-09-11T{clock}"


def _step(
    started: str, completed: str | None, elapsed: float | None, state: str = "completed"
) -> dict[str, Any]:
    entry: dict[str, Any] = {"state": state, "started_at": _ts(started)}
    if completed is not None:
        entry["completed_at"] = _ts(completed)
    if elapsed is not None:
        entry["elapsed_s"] = elapsed
    return entry


def _status(
    process: str, started: str, completed: str | None, steps: dict[str, Any]
) -> dict[str, Any]:
    status: dict[str, Any] = {
        "process": process,
        "started_at": _ts(started),
        "steps": steps,
        "state": "completed" if completed else "running",
    }
    if completed:
        status["completed_at"] = _ts(completed)
    return status


def _plan(scope_path: list[str], steps: list[tuple[str, str, str, list[str]]]) -> dict[str, Any]:
    return {
        "run_plan": {
            "schema": "metaproc:RunPlanSnapshot/0.1",
            "run_id": "/".join([f"{_ROOT_PROCESS}/run-1", *scope_path]),
            "scope_path": scope_path,
            "steps": [
                {
                    "step_id": step_id,
                    "mode": mode,
                    "task_shape": shape,
                    "item_keys": keys,
                    "outputs": {},
                    "fingerprint": "a" * 16,
                }
                for step_id, mode, shape, keys in steps
            ],
        }
    }


def _scope(
    run_dir: Path,
    parts: list[str],
    process: str,
    status: dict[str, Any],
    plan: list[tuple[str, str, str, list[str]]],
) -> Path:
    scope = run_dir.joinpath(*parts)
    _yaml(scope / ".state" / "process-status.yaml", status | {"process": process})
    _yaml(scope / ".state" / "run-plan.yaml", _plan(parts, plan))
    return scope


def _attempt(
    path: Path,
    *,
    step_id: str,
    number: int,
    disposition: str | None,
    failure_class: str | None = None,
    kinds: list[str] | None = None,
    error: str | None = None,
) -> None:
    record: dict[str, Any] = {
        "schema": "metaproc:TaskAttemptRecord/0.1",
        "attempt_id": f"att-20260911T100000Z.000000000{number}.abcdefghij",
        "run_id": f"{_ROOT_PROCESS}/run-1",
        "step_id": step_id,
        "attempt_number": number,
        "started_at": _ts("10:00:00"),
    }
    if disposition is not None:
        record |= {"disposition": disposition, "ended_at": _ts("10:01:00")}
    if failure_class:
        record["failure_class"] = failure_class
    if error:
        record["error"] = error
    if kinds:
        record["output_failures"] = [
            {"output": "out", "path": "out.md", "kind": kind, "message": kind} for kind in kinds
        ]
    _yaml(path / "attempt.yaml", record)


def _transcript(path: Path, lines: list[dict[str, Any] | str], invocation: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(line if isinstance(line, str) else json.dumps(line) for line in lines) + "\n"
    if path.name.endswith(".gz"):
        with gzip.open(path, "wt", encoding="utf-8") as stream:
            stream.write(body)
        logical = path.with_name(path.name[: -len(".gz")])
    else:
        path.write_text(body, encoding="utf-8")
        logical = path
    logical.with_name(logical.name + ".invocation.json").write_text(json.dumps(invocation))


def _gemini_result(duration_ms: int, *models: str) -> dict[str, Any]:
    return {
        "type": "result",
        "status": "success",
        "stats": {"duration_ms": duration_ms, "models": {model: {} for model in models}},
    }


def _jsonl(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


def _resource_summary(
    run_dir: Path,
    *,
    list_cost_usd: float = 2.5,
    unpriced_models: list[dict[str, Any]] | None = None,
) -> None:
    fmf_write(
        run_dir / "resource-usage-summary.md",
        "# Resource usage summary\n",
        {
            "softschema": {
                "contract": "metaproc.resources:ResourceUsageSummary/v1",
                "envelope": "resource_usage",
                "status": "enforced",
            },
            "resource_usage": {
                "run_id": f"{_ROOT_PROCESS}/run-1",
                "unpriced_models": unpriced_models or [],
                "totals": {
                    "input_tokens": 1000,
                    "output_tokens": 200,
                    "cache_read_tokens": 50,
                    "cache_write_tokens": 0,
                    "list_cost_usd": list_cost_usd,
                    "cpu_pct_avg": 50.0,
                    "cpu_pct_max": 120.0,
                    "rss_bytes_max": 2 * 1024**3,
                    "tool_calls": 7,
                },
                "provider_meters": [
                    {
                        "key": {
                            "provider": "google",
                            "product": "gemini-cli",
                            "meter": "agent_invocations",
                            "unit": "count",
                        },
                        "coverage": "measured",
                        "actual_quantity": 4.0,
                        "unmeasured_event_count": 0,
                        "source_event_ids": ["evt-0000000000000a"],
                    },
                    {
                        "key": {
                            "provider": "google",
                            "product": "gemini-cli",
                            "meter": "api_requests",
                            "unit": "count",
                        },
                        "coverage": "unmeasured",
                        "unmeasured_event_count": 4,
                        "source_event_ids": ["evt-0000000000000b"],
                    },
                ],
                "finalization": {
                    "state": "completed",
                    "trigger": "terminal",
                    "finalized_at": "2026-09-11T10:31:05Z",
                },
            },
        },
    )


def _composite_run(root: Path, name: str = "run-1") -> Path:
    """Build a two-item composite run whose every figure is computed in the tests."""
    run_dir = root / name
    _yaml(
        run_dir / ".state" / "run-config.yaml",
        {
            "metaproc_layout": "metaproc-run-layout/2",
            "process": _ROOT_PROCESS,
            "run_id": name,
            "variables": {
                "RUN_ID": name,
                "APP_REVISION": "abc123",
                "app_revision": "abc123",
            },
            "backend": "local",
            "git_sha": "abc123",
            "variant": "fast-model",
            "execution_profile": "fast-model",
            "resolved_profiles": [{"name": "fast-model", "config": {"model": "gemini-3.6-flash"}}],
        },
    )
    _scope(
        run_dir,
        [],
        _ROOT_PROCESS,
        _status(
            _ROOT_PROCESS,
            "10:00:00",
            "10:31:00",
            {
                "intake": _step("10:00:00", "10:00:10", 10.0),
                "author": _step("10:00:10", "10:20:10", 1200.0),
                "adopt": _step("10:20:10", "10:20:10", 0.0),
                "gate": _step("10:20:10", "10:20:30", 20.0),
                "depth": _step("10:20:30", "10:30:30", 600.0),
                "review": _step("10:30:30", "10:31:00", 30.0),
            },
        ),
        [
            ("intake", "composite", "scalar", []),
            ("author", "composite", "mapped", ["A", "B"]),
            ("adopt", "composite", "mapped", []),
            ("gate", "code", "scalar", []),
            ("depth", "composite", "mapped", ["A", "B"]),
            ("review", "code", "scalar", []),
        ],
    )
    item_plan = [("research", "agent", "scalar", []), ("write", "code", "scalar", [])]
    _scope(
        run_dir,
        ["author", "A"],
        _ITEM_PROCESS,
        _status(
            _ITEM_PROCESS,
            "10:00:10",
            "10:12:10",
            {
                "research": _step("10:00:10", "10:08:10", 480.0),
                "write": _step("10:08:10", "10:12:10", 240.0),
            },
        ),
        item_plan,
    )
    _scope(
        run_dir,
        ["author", "B"],
        _ITEM_PROCESS,
        _status(
            _ITEM_PROCESS,
            "10:00:10",
            "10:20:10",
            {
                "research": _step("10:00:10", "10:15:10", 900.0),
                "write": _step("10:15:10", "10:20:10", 300.0),
            },
        ),
        item_plan,
    )
    # A copied tree carrying a scope-shaped .state directory and no run plan.
    _yaml(
        run_dir / "author" / "A" / "research-copy" / "original" / ".state" / "process-status.yaml",
        _status(
            _ITEM_PROCESS,
            "10:00:10",
            "10:08:10",
            {"research": _step("10:00:10", "10:08:10", 480.0)},
        ),
    )
    depth_a = _scope(
        run_dir,
        ["depth", "A"],
        _DEPTH_PROCESS,
        _status(
            _DEPTH_PROCESS,
            "10:20:30",
            "10:25:30",
            {
                "fan": _step("10:20:30", "10:22:30", 120.0),
                "plan": _step("10:22:30", "10:25:30", 180.0),
            },
        ),
        [("fan", "code", "mapped", ["X"]), ("plan", "agent", "scalar", [])],
    )
    _yaml(
        depth_a / ".state" / "tasks" / "fan" / "X" / "status.yaml",
        {"state": "completed", "started_at": _ts("10:20:30"), "completed_at": _ts("10:22:00")},
    )
    depth_b = _scope(
        run_dir,
        ["depth", "B"],
        _DEPTH_PROCESS,
        _status(
            _DEPTH_PROCESS, "10:20:40", "10:30:30", {"plan": _step("10:20:40", "10:30:30", 590.0)}
        ),
        [("plan", "agent", "scalar", [])],
    )

    # Attempt records in all three path shapes, beside a legacy launch snapshot.
    tasks = run_dir / ".state" / "tasks"
    _attempt(
        tasks / "author" / "A" / "attempts" / "att-1",
        step_id="author",
        number=1,
        disposition="succeeded",
    )
    _attempt(
        tasks / "author" / "B" / "attempts" / "att-2",
        step_id="author",
        number=1,
        disposition="succeeded",
    )
    research_a = run_dir / "author" / "A" / ".state" / "tasks" / "research"
    _attempt(
        research_a / "attempts" / "att-3",
        step_id="research",
        number=1,
        disposition="retryable",
        failure_class="invalid_output",
        kinds=["missing"],
    )
    _attempt(
        research_a / "attempts" / "att-4", step_id="research", number=2, disposition="succeeded"
    )
    _yaml(research_a / "attempt.yaml", {"run_id": "legacy", "step_id": "research", "params": {}})
    research_b = run_dir / "author" / "B" / ".state" / "tasks" / "research"
    _attempt(
        research_b / "attempts" / "att-5",
        step_id="research",
        number=1,
        disposition="lost",
        error="orchestrator lost the worker",
    )
    _attempt(
        research_b / "attempts" / "att-6", step_id="research", number=2, disposition="succeeded"
    )
    fan_x = depth_a / ".state" / "tasks" / "fan" / "X"
    _attempt(
        fan_x / "attempts" / "att-7",
        step_id="fan",
        number=1,
        disposition="retryable",
        failure_class="invalid_output",
        kinds=["empty"],
    )
    _attempt(fan_x / "attempts" / "att-8", step_id="fan", number=2, disposition="succeeded")
    _attempt(
        depth_b / ".state" / "tasks" / "plan" / "attempts" / "att-9",
        step_id="plan",
        number=1,
        disposition=None,
    )

    # Transcripts: plain Gemini, gzipped Gemini with a substituted model, one with no
    # terminal result and a long tool-result line, and a Claude-shaped result.
    argv_36 = {"argv": ["gemini", "-m", "gemini-3.6-flash"], "metadata": {}}
    _transcript(
        run_dir / "author" / "A" / ".logs" / "tasks" / "research" / "research_default_1.jsonl",
        [{"type": "init"}, _gemini_result(400_000, "gemini-3.6-flash", "gemini-3-flash-preview")],
        argv_36,
    )
    _transcript(
        run_dir / "author" / "B" / ".logs" / "tasks" / "research" / "research_default_2.jsonl.gz",
        [{"type": "init"}, _gemini_result(800_000, "gemini-3.8-flash")],
        argv_36,
    )
    long_line = json.dumps({"type": "tool_result", "output": "x" * 6000})
    _transcript(
        depth_a / ".logs" / "tasks" / "plan" / "plan_default_3.jsonl",
        [{"type": "init"}, long_line, {"type": "message", "content": "cut off"}],
        {"metadata": {"execution_profile": "fast-model"}},
    )
    _transcript(
        depth_b / ".logs" / "tasks" / "plan" / "plan_default_4.jsonl",
        [{"type": "result", "duration_ms": 100_000, "modelUsage": {"claude-x": {}}}],
        {"argv": ["claude", "--model", "claude-x"]},
    )

    runpool = run_dir / ".logs" / "runpool"
    _jsonl(
        runpool / "events.jsonl",
        [
            {"event": "pool_start", "max_concurrency": 4, "ts": "2026-09-11T10:00:05+00:00"},
            {"event": "process_start", "pid": 1, "label": "a", "ts": "2026-09-11T10:00:10+00:00"},
            {"event": "process_start", "pid": 2, "label": "b", "ts": "2026-09-11T10:00:10+00:00"},
            {"event": "process_exit", "pid": 1, "label": "a", "ts": "2026-09-11T10:08:10+00:00"},
            {"event": "process_exit", "pid": 2, "label": "b", "ts": "2026-09-11T10:15:10+00:00"},
            {"event": "process_start", "pid": 4, "label": "d", "ts": "2026-09-11T10:20:40+00:00"},
            {"event": "process_start", "pid": 3, "label": "c", "ts": "2026-09-11T10:22:30+00:00"},
            {"event": "process_kill", "pid": 3, "label": "c", "ts": "2026-09-11T10:25:30+00:00"},
            {"event": "process_exit", "pid": 3, "label": "c", "ts": "2026-09-11T10:25:30+00:00"},
            {"event": "process_exit", "pid": 4, "label": "d", "ts": "2026-09-11T10:30:30+00:00"},
        ],
    )
    _jsonl(
        runpool / "health.jsonl",
        [
            _health("10:00:20", active=2, cap=2, swap=1.0, delta=0.0, disk=100.0),
            _health("10:05:00", active=2, cap=4, swap=1.5, delta=0.5, disk=90.0),
            _health("10:22:40", active=2, cap=2, swap=2.5, delta=0.2, disk=80.0),
            _health("10:26:00", active=4, cap=4, swap=2.0, delta=0.0, disk=85.0),
        ],
    )
    # Every agent step owns an admission stream that records auth and host admission
    # but never a pool start; a copied tree can carry a pool stream of its own.
    _jsonl(
        run_dir / "author" / "A" / ".logs" / "runpool" / "steps" / "research" / "events.jsonl",
        [{"event": "auth_outcome", "ts": "2026-09-11T10:00:10+00:00"}],
    )
    _jsonl(
        depth_b / ".logs" / "runpool" / "steps" / "plan" / "events.jsonl",
        [
            {
                "event": "host_admission_denied",
                "decision": "wait",
                "ts": "2026-09-11T10:20:40+00:00",
            }
        ],
    )
    _jsonl(
        run_dir
        / "author"
        / "A"
        / "research-copy"
        / "original"
        / ".logs"
        / "runpool"
        / "events.jsonl",
        [
            {"event": "pool_start", "max_concurrency": 9, "ts": "2026-09-11T10:00:05+00:00"},
            {"event": "process_start", "pid": 9, "label": "x", "ts": "2026-09-11T10:00:10+00:00"},
        ],
    )
    _resource_summary(run_dir)
    return run_dir


def _health(
    clock: str, *, active: int, cap: int, swap: float, delta: float, disk: float
) -> dict[str, Any]:
    return {
        "event": "health_sample",
        "active_count": active,
        "current_concurrency": cap,
        "swap_used_gb": swap,
        "swap_delta_gb_per_min": delta,
        "disk_free_gb": disk,
        "ts": f"2026-09-11T{clock}+00:00",
    }


def _bare_run(root: Path) -> Path:
    """A run root with process status only: no plan, pool, transcripts, or resources."""
    run_dir = root / "bare"
    _yaml(
        run_dir / ".state" / "process-status.yaml",
        _status("bare", "09:00:00", None, {"only": _step("09:00:00", None, None, state="running")}),
    )
    return run_dir


@pytest.fixture
def composite_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(ops, "TOOL_RESULT_CAP_BYTES", 4096)
    return _composite_run(tmp_path)


def _summary(run_dir: Path) -> AgentOperationsSummary:
    return ops.build_operations_summary(run_dir, generated_at=datetime(2026, 9, 12, tzinfo=UTC))


# ── Fold ──────────────────────────────────────────────────────────


def test_run_setup_and_stage_figures(composite_run: Path) -> None:
    summary = _summary(composite_run)

    assert summary.unavailable == {}
    run = summary.run
    assert run is not None
    assert run.elapsed_s == 1860.0
    assert (run.state, run.state_source) == ("completed", "resource_summary")
    assert run.item_count == 2
    assert run.revisions == {"git_sha": "abc123", "APP_REVISION": "abc123"}
    assert (run.variant, run.execution_profile, run.backend) == (
        "fast-model",
        "fast-model",
        "local",
    )

    setup = summary.setup
    assert setup is not None
    assert setup.step_ids == ["intake", "gate", "review"]
    assert setup.elapsed_s == 60.0
    assert setup.share_of_elapsed == round(60 / 1860, 4)

    stages = {row.step_id: row for row in summary.stages or []}
    assert stages["author"].share_of_elapsed == round(1200 / 1860, 4)
    assert stages["adopt"].task_shape == "mapped"
    assert stages["adopt"].item_count == 0


def test_per_item_chains_barriers_and_slowest(composite_run: Path) -> None:
    items = _summary(composite_run).items
    assert items is not None
    assert items.stages == ["author", "depth"]
    chains = {chain.item_key: chain for chain in items.items}

    assert chains["A"].chain_running_s == 720 + 300
    assert chains["A"].barrier_wait_s == 500
    assert chains["A"].chain_span_s == 1520
    assert chains["B"].chain_running_s == 1200 + 590
    assert chains["B"].barrier_wait_s == 30
    assert chains["B"].chain_span_s == 1820
    assert chains["A"].stages[0].unavailable["wait_before_s"] == "first mapped stage for this item"

    assert items.chain_running_s is not None
    assert (items.chain_running_s.p50, items.chain_running_s.p90, items.chain_running_s.max) == (
        1405.0,
        1713.0,
        1790.0,
    )
    assert [chain.item_key for chain in items.slowest] == ["B", "A"]
    slowest = items.slowest[0].slowest_step
    assert slowest is not None
    assert (slowest.stage, slowest.step_id, slowest.elapsed_s) == ("author", "research", 900.0)

    author = next(stage for stage in items.per_stage if stage.stage == "author")
    research = next(step for step in author.steps if step.step_id == "research")
    assert (research.mode, research.elapsed_s.count, research.elapsed_s.mean) == ("agent", 2, 690.0)


def test_steps_ignore_copied_state_and_split_agent_from_code(composite_run: Path) -> None:
    steps = _summary(composite_run).steps
    assert steps is not None
    assert steps.scope_count == 5
    assert steps.ignored_state_dirs == 1
    rows = {(row.process, row.step_id): row for row in steps.rows}

    assert rows[(_ITEM_PROCESS, "research")].elapsed_s.count == 2
    assert rows[(_DEPTH_PROCESS, "fan")].elapsed_s.total == 90.0
    assert rows[(_ROOT_PROCESS, "author")].elapsed_s.total == 720 + 1200
    assert steps.agent_total_s == 480 + 900 + 180 + 590
    assert steps.code_total_s == 240 + 300 + 90 + 20 + 30


def _nest_under_parent_run(run_dir: Path, prefix: list[str]) -> None:
    """Record every plan path of *run_dir* from the root of a run that holds it at *prefix*.

    A run executed as a child scope of a larger run writes plans the way the larger run
    addresses them, so the run's own root plan names *prefix* rather than an empty path.
    """
    for plan_path in run_dir.rglob("run-plan.yaml"):
        document = yaml.safe_load(plan_path.read_text())
        plan = document["run_plan"]
        plan["scope_path"] = [*prefix, *plan["scope_path"]]
        plan["run_id"] = "/".join([f"{_ROOT_PROCESS}/batch-1", *plan["scope_path"]])
        plan_path.write_text(to_yaml_string(document))


def test_a_run_executed_inside_a_parent_run_keeps_its_nested_scopes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read in place or hydrated alone, the child run's scopes and transcripts are its own."""
    monkeypatch.setattr(ops, "TOOL_RESULT_CAP_BYTES", 4096)
    prefix = ["cohorts", "week-1", "main", "run-1"]
    in_place = _composite_run(tmp_path.joinpath("batch", *prefix[:-1]))
    _nest_under_parent_run(in_place, prefix)
    hydrated = tmp_path / "hydrated" / "run-1"
    shutil.copytree(in_place, hydrated)

    for run_dir in (in_place, hydrated):
        summary = _summary(run_dir)
        assert summary.steps is not None and summary.agents is not None
        assert (summary.steps.scope_count, summary.steps.ignored_state_dirs) == (5, 1)
        assert (summary.agents.transcripts, summary.agents.provider_s) == (4, 1300.0)
        assert summary.items is not None
        assert {chain.item_key: chain.chain_running_s for chain in summary.items.items} == {
            "A": 720 + 300,
            "B": 1200 + 590,
        }

    # A scope whose plan names its path from the child run's own root, not the parent's,
    # is a copy from another tree and stays ignored.
    stray = hydrated / "depth" / "B" / ".state" / "run-plan.yaml"
    document = yaml.safe_load(stray.read_text())
    document["run_plan"]["scope_path"] = ["depth", "B"]
    stray.write_text(to_yaml_string(document))
    steps = _summary(hydrated).steps
    assert steps is not None
    assert (steps.scope_count, steps.ignored_state_dirs) == (4, 2)


def test_attempt_records_in_three_shapes(composite_run: Path) -> None:
    retries = _summary(composite_run).retries
    assert retries is not None

    assert retries.attempt_records == 9
    assert retries.path_shapes == {"child_item": 2, "child_step": 5, "root_item": 2}
    assert retries.by_disposition == {"lost": 1, "retryable": 2, "succeeded": 5}
    assert retries.by_failure_class == {"invalid_output": 2, "unclassified": 1}
    assert retries.live_attempts == 1
    assert retries.wrote_nothing == 1
    assert retries.non_record_attempt_files == 1
    research = next(row for row in retries.by_step if row.step_id == "research")
    assert (research.attempts, research.not_succeeded) == (4, 2)
    assert research.by_failure_class == {"invalid_output": 1, "unclassified": 1}


def test_parallelism_replays_pool_events_and_samples(composite_run: Path) -> None:
    parallelism = _summary(composite_run).parallelism
    assert parallelism is not None

    assert parallelism.ceiling == 4
    assert parallelism.peak_running == 2
    assert parallelism.mean_running == round(2150 / 1820, 3)
    assert parallelism.health_samples == 4
    assert parallelism.share_samples_at_cap == 0.75
    assert parallelism.share_samples_at_ceiling == 0.25
    assert (parallelism.pools[0].cap_min, parallelism.pools[0].cap_max) == (2, 4)


def test_agents_read_transcript_results_models_and_capped_lines(composite_run: Path) -> None:
    agents = _summary(composite_run).agents
    assert agents is not None

    assert agents.transcripts == 4
    assert agents.transcripts_without_result == 1
    assert agents.provider_s == 1300.0
    assert agents.requested_models == {"claude-x": 1, "gemini-3.6-flash": 3}
    assert agents.served_models == {
        "claude-x": 1,
        "gemini-3-flash-preview": 1,
        "gemini-3.6-flash": 1,
        "gemini-3.8-flash": 1,
    }
    assert agents.model_mismatches == 1
    assert agents.tool_results_at_cap == 1
    assert agents.oversized_transcripts == ["depth/A/.logs/tasks/plan/plan_default_3.jsonl"]
    assert agents.tokens is not None
    assert agents.tokens.input_tokens == 1000
    assert [meter.coverage.value for meter in agents.meters] == ["measured", "unmeasured"]


def test_resources_from_summary_health_and_bounded_walk(
    composite_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = _summary(composite_run).resources
    assert resources is not None

    assert (resources.list_cost_usd, resources.rss_bytes_max) == (2.5, 2 * 1024**3)
    assert (resources.swap_used_peak_gb, resources.swap_growth_gb) == (2.5, 1.5)
    assert (resources.swap_delta_max_gb_per_min, resources.disk_free_min_gb) == (0.5, 80.0)
    assert resources.run_size_bytes is not None and resources.run_size_bytes > 0
    assert resources.unavailable == {"actual_cost_usd": "resource summary reports it unmeasured"}

    monkeypatch.setattr(ops, "RUN_SIZE_WALK_LIMIT", 3)
    bounded = _summary(composite_run).resources
    assert bounded is not None
    assert bounded.run_size_bytes is None
    assert bounded.unavailable["run_size_bytes"] == "walk stopped after 3 entries"


def test_missing_evidence_is_null_with_a_reason(tmp_path: Path) -> None:
    summary = _summary(_bare_run(tmp_path))

    assert summary.run is not None
    assert summary.run.state == "running"
    assert summary.run.elapsed_s is None
    assert (
        summary.run.unavailable["elapsed_s"] == "process status lacks a start or a completion time"
    )
    assert summary.items is None
    assert "run-plan.yaml" in summary.unavailable["items"]
    assert summary.parallelism is None
    assert summary.unavailable["parallelism"] == "no RunPool event stream exists in any scope"
    assert summary.resources is not None
    assert summary.resources.list_cost_usd is None
    assert summary.resources.unavailable["list_cost_usd"] == "resource-usage-summary.md is absent"
    assert summary.agents is not None
    assert summary.agents.provider_s is None


def test_a_null_figure_without_a_reason_is_rejected() -> None:
    fields = dict.fromkeys(RunFigures.model_fields)
    fields.pop("unavailable")
    with pytest.raises(ValidationError, match="null without a reason"):
        RunFigures.model_validate(fields)
    with pytest.raises(ValidationError, match="both a value and an unavailable reason"):
        RunFigures.model_validate(
            {**fields, "process": "p", "unavailable": dict.fromkeys(fields, "gone")}
        )


def _edit_yaml(path: Path, edit: Callable[[dict[str, Any]], None]) -> None:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    edit(document)
    path.write_text(to_yaml_string(document), encoding="utf-8")


def _move_scope_window(scope: Path, step_id: str, started: str, completed: str) -> None:
    """Move a single-step stage scope, and that step, to one start and completion."""

    def edit(status: dict[str, Any]) -> None:
        status["started_at"] = _ts(started)
        status["completed_at"] = _ts(completed)
        status["steps"] = {step_id: status["steps"][step_id]}
        status["steps"][step_id].update(started_at=_ts(started), completed_at=_ts(completed))
        status["steps"][step_id].pop("elapsed_s", None)

    _edit_yaml(scope / ".state" / "process-status.yaml", edit)


def test_items_pair_stages_in_start_order_and_never_report_a_negative_wait(
    composite_run: Path,
) -> None:
    """Mapped stages with no edge between them may overlap or run out of declared order."""
    # A's depth pass starts at 10:05:00, before A's authoring completes at 10:12:10.
    _move_scope_window(composite_run / "depth" / "A", "plan", "10:05:00", "10:25:30")
    # B's depth pass runs and completes before B's authoring starts at 10:00:10.
    _move_scope_window(composite_run / "depth" / "B", "plan", "09:40:00", "09:50:00")

    items = _summary(composite_run).items
    assert items is not None
    chains = {chain.item_key: chain for chain in items.items}

    a_depth = next(stage for stage in chains["A"].stages if stage.stage == "depth")
    assert a_depth.wait_before_s is None
    assert a_depth.unavailable["wait_before_s"] == (
        "this stage started before the previous stage completed"
    )
    assert chains["A"].barrier_wait_s is None
    assert chains["A"].unavailable["barrier_wait_s"] == "stages overlap for this item"
    assert chains["A"].chain_span_s == 1520

    assert [stage.stage for stage in chains["B"].stages] == ["depth", "author"]
    assert chains["B"].stages[1].wait_before_s == 610
    assert chains["B"].barrier_wait_s == 610

    assert items.barrier_wait_s is not None
    assert (items.barrier_wait_s.count, items.barrier_wait_s.total) == (1, 610.0)
    assert all(
        stage.wait_before_s is None or stage.wait_before_s >= 0
        for chain in items.items
        for stage in chain.stages
    )


def test_a_scope_whose_plan_cannot_be_read_is_kept_and_named(composite_run: Path) -> None:
    (composite_run / "author" / "B" / ".state" / "run-plan.yaml").write_text(
        "run_plan: [unclosed\n"
    )

    summary = _summary(composite_run)

    steps = summary.steps
    assert steps is not None and summary.agents is not None and summary.retries is not None
    assert (steps.scope_count, steps.ignored_state_dirs) == (5, 1)
    assert list(steps.unreadable_plans) == ["author/B/.state/run-plan.yaml"]
    assert (summary.agents.transcripts, summary.retries.attempt_records) == (4, 9)
    body = render_summary_markdown(summary)
    assert "1 copied `.state` directory ignored" in body
    assert "`author/B/.state/run-plan.yaml`" in body


def test_an_unreadable_root_plan_keeps_the_scopes_of_a_run_nested_in_a_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ops, "TOOL_RESULT_CAP_BYTES", 4096)
    prefix = ["cohorts", "week-1", "main", "run-1"]
    run_dir = _composite_run(tmp_path.joinpath("batch", *prefix[:-1]))
    _nest_under_parent_run(run_dir, prefix)
    (run_dir / ".state" / "run-plan.yaml").write_text("run_plan: [unclosed\n")

    summary = _summary(run_dir)

    assert summary.steps is not None and summary.agents is not None
    assert (summary.steps.scope_count, summary.steps.ignored_state_dirs) == (5, 1)
    assert list(summary.steps.unreadable_plans) == [".state/run-plan.yaml"]
    assert summary.agents.transcripts == 4


def test_parallelism_reports_pools_and_counts_streams_that_started_nothing(
    composite_run: Path,
) -> None:
    """Agent step admission streams and copied trees add no pool rows."""
    parallelism = _summary(composite_run).parallelism
    assert parallelism is not None

    assert [pool.source for pool in parallelism.pools] == [".logs/runpool/events.jsonl"]
    assert parallelism.empty_streams == 2
    assert parallelism.ceiling == 4


def test_pressure_checks_stand_in_for_absent_health_samples_under_their_own_name(
    composite_run: Path,
) -> None:
    runpool = composite_run / ".logs" / "runpool"
    (runpool / "health.jsonl").unlink()
    with (runpool / "events.jsonl").open("a") as stream:
        for clock, active, cap in (("10:00:20", 2, 2), ("10:05:00", 1, 4)):
            pressure = {"event": "pressure_check", "active_count": active}
            stream.write(json.dumps(pressure | {"current_concurrency": cap, "ts": _ts(clock)}))
            stream.write("\n")

    parallelism = _summary(composite_run).parallelism
    assert parallelism is not None
    pool = parallelism.pools[0]

    assert (pool.health_samples, pool.pressure_checks, pool.sample_source) == (
        0,
        2,
        "pressure_check",
    )
    assert pool.share_samples_at_cap == 0.5
    assert (parallelism.health_samples, parallelism.pressure_checks) == (0, 2)
    assert parallelism.sample_source == "pressure_check"
    body = render_summary_markdown(_summary(composite_run))
    assert "of 2 pressure checks were at the current cap" in body


def test_retries_count_attempts_beyond_the_first_and_keep_processes_apart(
    composite_run: Path,
) -> None:
    """A failed attempt that was never retried is not a retry."""
    research_a = composite_run / "author" / "A" / ".state" / "tasks" / "research"
    research_b = composite_run / "author" / "B" / ".state" / "tasks" / "research"
    shutil.rmtree(research_a / "attempts" / "att-4")
    shutil.rmtree(research_b / "attempts" / "att-6")
    _attempt(
        composite_run / "depth" / "B" / ".state" / "tasks" / "research" / "attempts" / "att-10",
        step_id="research",
        number=1,
        disposition="failed",
        failure_class="invalid_output",
    )

    summary = _summary(composite_run)
    retries = summary.retries
    assert retries is not None

    assert (retries.attempt_records, retries.retries) == (8, 1)
    rows = {(row.process, row.step_id): row for row in retries.by_step}
    assert (
        rows[(_ITEM_PROCESS, "research")].attempts,
        rows[(_ITEM_PROCESS, "research")].not_succeeded,
    ) == (2, 2)
    assert (
        rows[(_DEPTH_PROCESS, "research")].attempts,
        rows[(_DEPTH_PROCESS, "research")].not_succeeded,
    ) == (1, 1)

    ops.write_operations_summary(summary, composite_run)
    row = build_operations_rollup([composite_run], target_min_s=0, target_max_s=3600).rows[0]
    assert (row.retries, row.not_succeeded) == (1, 4)


def test_a_list_cost_that_leaves_out_unpriced_models_reads_as_a_lower_bound(
    composite_run: Path,
) -> None:
    _resource_summary(
        composite_run,
        list_cost_usd=1.0,
        unpriced_models=[{"model": "mystery-model", "invocations": 3}],
    )

    summary = _summary(composite_run)
    resources = summary.resources
    assert resources is not None
    assert resources.list_cost_usd == 1.0
    assert [(entry.model, entry.invocations) for entry in resources.unpriced_models] == [
        ("mystery-model", 3)
    ]
    body = render_summary_markdown(summary)
    assert "| List cost | at least USD 1.00 |" in body
    assert "| `mystery-model` | 3 |" in body

    path = ops.write_operations_summary(summary, composite_run)
    validation = _validate_written_summary(path, composite_run)
    assert validation.ok, (validation.structural.errors, validation.semantic.errors)
    rollup = build_operations_rollup([composite_run], target_min_s=0, target_max_s=3600)
    row = rollup.rows[0]
    assert (row.list_cost_usd, row.list_cost_per_item_usd, row.unpriced_invocations) == (
        1.0,
        0.5,
        3,
    )
    assert "| at least USD 0.50 |" in render_rollup_markdown(rollup)


def test_agents_table_shows_every_token_bucket(composite_run: Path) -> None:
    body = render_summary_markdown(_summary(composite_run))
    assert "| Cache read tokens | 50 |" in body
    assert "| Cache write tokens | 0 |" in body


def _wide_run(root: Path, item_count: int) -> Path:
    """A root with two mapped stages over *item_count* items, shaped like a batch root."""
    run_dir = root / "wide"
    keys = [f"item-{index:03d}" for index in range(item_count)]

    def write(path: Path, data: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    write(
        run_dir / ".state" / "process-status.yaml",
        _status(
            _ROOT_PROCESS,
            "10:00:00",
            "12:00:00",
            {
                "author": _step("10:00:00", "11:00:00", 3600.0),
                "depth": _step("11:00:00", "12:00:00", 3600.0),
            },
        ),
    )
    write(
        run_dir / ".state" / "run-plan.yaml",
        _plan(
            [],
            [("author", "composite", "mapped", keys), ("depth", "composite", "mapped", keys)],
        ),
    )
    item_steps = [(f"step-{index}", "agent", "scalar", []) for index in range(4)]
    for stage, start, end in (("author", "10:00", "11:00"), ("depth", "11:00", "12:00")):
        for key in keys:
            scope = run_dir / stage / key
            steps = {
                step_id: _step(f"{start}:00", f"{end}:00", 3600.0) for step_id, *_rest in item_steps
            }
            write(
                scope / ".state" / "process-status.yaml",
                _status(_ITEM_PROCESS, f"{start}:00", f"{end}:00", steps),
            )
            write(scope / ".state" / "run-plan.yaml", _plan([stage, key], item_steps))
            for step_id, *_rest in item_steps:
                write(
                    scope / ".logs" / "runpool" / "steps" / step_id / "events.jsonl",
                    {"event": "auth_outcome", "ts": "2026-09-11T10:00:00+00:00"},
                )
    runpool = run_dir / ".logs" / "runpool"
    _jsonl(
        runpool / "events.jsonl",
        [
            {"event": "pool_start", "max_concurrency": 40, "ts": "2026-09-11T10:00:00+00:00"},
            {"event": "process_start", "pid": 1, "label": "a", "ts": "2026-09-11T10:00:00+00:00"},
            {"event": "process_exit", "pid": 1, "label": "a", "ts": "2026-09-11T12:00:00+00:00"},
        ],
    )
    return run_dir


def test_a_wide_root_folds_to_one_pool_row_in_bounded_time_and_size(tmp_path: Path) -> None:
    """Two hundred items with four agent steps per stage scope: 1,600 admission streams."""
    run_dir = _wide_run(tmp_path, 200)

    summary = _summary(run_dir)

    assert summary.unavailable == {}
    assert summary.items is not None and summary.steps is not None
    assert summary.parallelism is not None
    assert (summary.items.item_count, summary.steps.scope_count) == (200, 401)
    assert len(summary.parallelism.pools) == 1
    assert summary.parallelism.empty_streams == 1600
    # Generous bounds that still catch a per-stream or per-scope blow-up on a busy host.
    assert summary.extraction_s is not None and summary.extraction_s < 60
    written = ops.write_operations_summary(summary, run_dir)
    assert written.stat().st_size < 1024 * 1024


def test_long_result_line_after_a_capped_line_in_gzip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ops, "TOOL_RESULT_CAP_BYTES", 1024)
    monkeypatch.setattr(ops, "SCAN_CHUNK_BYTES", 256)
    path = tmp_path / "t.jsonl.gz"
    long_line = json.dumps({"type": "tool_result", "output": "y" * 5000})
    result = _gemini_result(5_000, "m") | {"padding": "z" * 900}
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write(long_line + "\n" + json.dumps(result) + "\n")

    reading = ops._read_transcript(path, ops._logical_size(path))

    assert reading.capped_tool_results == 1
    assert reading.result is not None
    assert reading.result["stats"]["duration_ms"] == 5_000


# ── Written document ──────────────────────────────────────────────


def _validate_written_summary(path: Path, run_dir: Path) -> ArtifactValidationResult:
    return validate_artifact(
        path,
        contract=Contract(
            id=OPERATIONS_SUMMARY_CONTRACT,
            model=AgentOperationsSummary,
            envelope_key="agent_operations",
            status=SchemaStatus.enforced,
            schema_path=run_dir / ops.OPERATIONS_SUMMARY_SCHEMA_RELATIVE,
        ),
    )


def test_written_summary_validates_against_its_registered_contract(composite_run: Path) -> None:
    summary = _summary(composite_run)
    path = ops.write_operations_summary(summary, composite_run)

    metadata = fmf_read_frontmatter(path)
    assert metadata is not None
    assert metadata["softschema"]["contract"] == OPERATIONS_SUMMARY_CONTRACT
    validation = _validate_written_summary(path, composite_run)
    assert validation.ok, (validation.structural.errors, validation.semantic.errors)
    assert ops.read_operations_summary(composite_run).summary == summary

    contract = get_plugin_registry().softschemas.resolve(OPERATIONS_SUMMARY_CONTRACT)
    assert contract is not None
    assert contract.model is AgentOperationsSummary


def test_enforced_contract_rejects_an_undeclared_key_in_a_nested_nullable_section(
    composite_run: Path,
) -> None:
    """``agents`` and its ``tokens`` are both nullable model references.

    The enforced profile refuses an outer nullable reference whose target would need an
    inferred closure, so the contract states closure at each nullable model reference.
    That stated closure has to keep rejecting a key the nested model does not declare.

    The error path is ``("agents",)`` rather than ``("agents", "tokens")`` because the
    stated closure on the outer ``agents`` wrapper reports the violation, not the nested
    ``TokenFigures`` model. A validator that reports the deeper path is an improvement,
    not a regression; the rejection itself is what this test guards.
    """
    path = ops.write_operations_summary(_summary(composite_run), composite_run)
    assert _validate_written_summary(path, composite_run).structural.ok
    metadata = fmf_read_frontmatter(path)
    assert metadata is not None
    metadata["agent_operations"]["agents"]["tokens"]["unmodeled_tokens"] = 1
    fmf_write(path, "# Operations summary\n", metadata)

    structural = _validate_written_summary(path, composite_run).structural

    assert structural.errors
    assert {(error["kind"], tuple(error.get("path", ()))) for error in structural.errors} == {
        ("schema_violation", ("agents",))
    }, structural.errors


def test_committed_operations_summary_schema_has_no_drift() -> None:
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "metaproc"
        / "data"
        / "schemas"
        / "agent-operations-summary.v1.schema.yaml"
    )
    result = compile_model(
        AgentOperationsSummary,
        schema_path,
        contract_id=OPERATIONS_SUMMARY_CONTRACT,
        check_only=True,
    )
    assert result.drift is False, result.drift_diff


def test_rendered_body_shows_the_structured_figures(composite_run: Path) -> None:
    body = render_summary_markdown(_summary(composite_run))

    for expected in (
        "| Elapsed | 31.0 min (1,860.0 s) |",
        "| `author` | mapped | 2 | completed | 1,200.0 s | 64.5% |",
        "| Chain running | 2 | 23.4 min | 28.6 min | 29.8 min | 23.4 min |",
        "| `B` | 29.8 min | 0.5 min | 30.3 min | `author` / `research` (15.0 min) |",
        "| Invocations missing the requested model | 1 |",
        "| Swap peak | 2.50 GB |",
        "| resources | `actual_cost_usd` | resource summary reports it unmeasured |",
    ):
        assert expected in body


def test_markdown_table_escapes_cells() -> None:
    table = render_markdown_table(["a", "b"], [["x|y", "line\nbreak"]], align=["left", "right"])
    assert table.splitlines() == ["| a | b |", "| --- | ---: |", "| x\\|y | line break |"]
    with pytest.raises(ValueError, match="one cell per header|cells for"):
        render_markdown_table(["a"], [["1", "2"]])


# ── Documents an earlier writer wrote ─────────────────────────────

_EARLIER_DOCUMENTS = Path(__file__).resolve().parent / "fixtures" / "operations_summary_v1"
"""A real run's artifacts, written before ``sample_source`` and ``RetryStepRow.process``.

Only the process name and the item keys are replaced, so the document keeps the shape
the earlier writer produced.
"""


def _earlier_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run-earlier"
    (run_dir / ".state").mkdir(parents=True)
    for name in ("operations-summary.md", "resource-usage-summary.md"):
        shutil.copy(_EARLIER_DOCUMENTS / name, run_dir / name)
    return run_dir


def test_a_summary_written_before_the_added_fields_still_reads(tmp_path: Path) -> None:
    """The fields added under this contract id are absent, and the rest must survive.

    A reader that cannot parse the document loses the run's process, which is what the
    consumer needs to know which process produced the run.
    """
    read = ops.read_operations_summary(_earlier_run(tmp_path))

    assert read.error is None
    summary = read.summary
    assert summary is not None
    assert summary.extractor_version == 1
    assert summary.run is not None
    assert summary.run.process == "example-roster-fit"
    assert summary.run.state == "failed"

    parallelism = summary.parallelism
    assert parallelism is not None
    assert parallelism.sample_source is None
    assert "sample_source" not in parallelism.unavailable
    assert [pool.sample_source for pool in parallelism.pools] == [None]
    assert parallelism.health_samples == 326

    retries = summary.retries
    assert retries is not None
    assert [(row.process, row.step_id) for row in retries.by_step] == [
        (None, "review-ticker"),
        (None, "judge-fit-gemini-flash-38"),
        (None, "judge-fit-gemini-flash-36"),
    ]


def test_an_earlier_summary_passes_the_contract_that_current_writers_compile_to(
    tmp_path: Path,
) -> None:
    """The Python reader is not the only reader; the shipped JSON Schema must agree."""
    run_dir = _earlier_run(tmp_path)
    shipped = Path(__file__).resolve().parents[1] / "src" / "metaproc" / "data" / "schemas"

    for name, contract_id, model, envelope, schema in (
        (
            "operations-summary.md",
            OPERATIONS_SUMMARY_CONTRACT,
            AgentOperationsSummary,
            "agent_operations",
            "agent-operations-summary.v1.schema.yaml",
        ),
        (
            "resource-usage-summary.md",
            RESOURCE_USAGE_SUMMARY_CONTRACT,
            ResourceUsageSummary,
            "resource_usage",
            "resource-usage-summary.v1.schema.yaml",
        ),
    ):
        validation = validate_artifact(
            run_dir / name,
            contract=Contract(
                id=contract_id,
                model=model,
                envelope_key=envelope,
                status=SchemaStatus.enforced,
                schema_path=shipped / schema,
            ),
        )
        assert validation.ok, (name, validation.structural.errors, validation.semantic.errors)


def _edit_summary(path: Path, edit: Callable[[dict[str, Any]], None]) -> None:
    """Rewrite one written summary's frontmatter, keeping it a frontmatter document."""
    metadata = fmf_read_frontmatter(path)
    assert metadata is not None
    edit(metadata)
    fmf_write(path, "# Operations summary\n", metadata)


def test_an_absent_summary_reads_as_absent(tmp_path: Path) -> None:
    read = ops.read_operations_summary(tmp_path)

    assert (read.absent, read.unreadable) == (True, False)
    assert (read.summary, read.error) == (None, None)


def test_a_malformed_summary_reads_as_unreadable_and_names_its_path(
    composite_run: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A document that no writer of any vintage could have produced stays an error.

    Reporting it as absent is what let a consumer recompute a run it should have read.
    """
    path = ops.write_operations_summary(_summary(composite_run), composite_run)
    _edit_summary(path, lambda data: data["agent_operations"]["run"].update(elapsed_s="soon"))

    with caplog.at_level(logging.WARNING):
        read = ops.read_operations_summary(composite_run)

    assert (read.absent, read.unreadable) == (False, True)
    assert read.summary is None
    assert read.error is not None
    assert "elapsed_s" in read.error
    assert str(path) in caplog.text
    assert "elapsed_s" in caplog.text


def test_a_summary_under_another_contract_reads_as_unreadable(composite_run: Path) -> None:
    path = ops.write_operations_summary(_summary(composite_run), composite_run)
    _edit_summary(path, lambda data: data["softschema"].update(contract="other:Thing/v1"))

    read = ops.read_operations_summary(composite_run)

    assert read.unreadable
    assert read.error is not None
    assert "other:Thing/v1" in read.error


def test_a_rollup_says_when_it_recomputed_because_a_summary_did_not_read(
    composite_run: Path,
) -> None:
    """Recomputing is right; doing it silently is what hid the break."""
    rows = build_operations_rollup([composite_run], target_min_s=0, target_max_s=3600).rows
    assert [row.summary_source for row in rows] == ["computed"]

    ops.write_operations_summary(_summary(composite_run), composite_run)
    rows = build_operations_rollup([composite_run], target_min_s=0, target_max_s=3600).rows
    assert [row.summary_source for row in rows] == ["written"]

    _edit_summary(
        composite_run / ops.OPERATIONS_SUMMARY_FILE,
        lambda data: data["agent_operations"]["run"].update(elapsed_s="soon"),
    )
    rows = build_operations_rollup([composite_run], target_min_s=0, target_max_s=3600).rows

    assert [row.summary_source for row in rows] == ["unreadable"]
    assert rows[0].elapsed_s == 1860.0


def test_a_writer_must_still_explain_a_null_it_states() -> None:
    """The pairing rule binds what a document says, so it still binds every writer.

    The fold names every figure it builds, so a null it leaves unexplained is rejected
    as it is written. Only a field the document never mentions is exempt.
    """
    with pytest.raises(ValidationError, match="sample_source is null without a reason"):
        PoolRow(source="events.jsonl", process_starts=0, health_samples=0, sample_source=None)

    unstated = PoolRow(source="events.jsonl", process_starts=0, health_samples=0)
    assert (unstated.sample_source, unstated.ceiling) == (None, None)


# ── Finalization ──────────────────────────────────────────────────


def _finalized_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run-final"
    run_dir.mkdir()
    _write_run_config(
        run_dir,
        process_name="example",
        process_path=tmp_path / "removed.process.md",
        run_id="run-final",
        variables={"RUN_ID": "run-final"},
        backend="local",
        variant=None,
    )
    _yaml(
        run_dir / ".state" / "process-status.yaml",
        _status("example", "10:00:00", "10:00:30", {"noop": _step("10:00:00", "10:00:30", 30.0)}),
    )
    finalize_run_resources(run_dir, outcome=FinalizationState.COMPLETED)
    return run_dir


def test_finalization_writes_no_jsonl_and_keeps_resources_fresh(tmp_path: Path) -> None:
    run_dir = _finalized_run(tmp_path)
    assert resource_artifacts_need_recovery(run_dir) is False
    jsonl_before = sorted(run_dir.rglob("*.jsonl"))

    written = ops.finalize_operations_summary(run_dir, outcome=FinalizationState.CANCELLED)

    assert written == run_dir / "operations-summary.md"
    assert sorted(run_dir.rglob("*.jsonl")) == jsonl_before
    assert resource_artifacts_need_recovery(run_dir) is False
    summary = ops.read_operations_summary(run_dir).summary
    assert summary is not None and summary.run is not None
    assert (summary.trigger, summary.run.state, summary.run.state_source) == (
        "finalization",
        "cancelled",
        "finalization",
    )


def test_finalization_failure_is_logged_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    run_dir = _finalized_run(tmp_path)

    def explode(*_args: object, **_kwargs: object) -> AgentOperationsSummary:
        raise RuntimeError("boom")

    monkeypatch.setattr(ops, "build_operations_summary", explode)
    with caplog.at_level(logging.ERROR, logger=ops.__name__):
        assert ops.finalize_operations_summary(run_dir, outcome=FinalizationState.COMPLETED) is None
    assert "operations summary failed" in caplog.text
    assert not (run_dir / "operations-summary.md").exists()


def test_a_failing_section_is_recorded_and_others_survive(
    composite_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_args: object) -> None:
        raise OSError("disk went away")

    monkeypatch.setattr(ops, "_retry_figures", explode)
    summary = _summary(composite_run)

    assert summary.retries is None
    assert summary.unavailable == {"retries": "OSError: disk went away"}
    assert summary.items is not None


# ── CLI ───────────────────────────────────────────────────────────


def _tree_state(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_summary_command_is_read_only_unless_write(composite_run: Path) -> None:
    before = _tree_state(composite_run)

    for fmt in ("md", "yaml", "json"):
        result = runner.invoke(app, ["operations", "summary", str(composite_run), "--format", fmt])
        assert result.exit_code == 0, result.output
    assert _tree_state(composite_run) == before

    json_result = runner.invoke(
        app, ["operations", "summary", str(composite_run), "--format", "json"]
    )
    assert json.loads(json_result.stdout)["run"]["elapsed_s"] == 1860.0

    written = runner.invoke(app, ["operations", "summary", str(composite_run), "--write"])
    assert written.exit_code == 0, written.output
    created = set(_tree_state(composite_run)) - set(before)
    assert created == {
        "operations-summary.md",
        ".state/schemas/agent-operations-summary.v1.schema.yaml",
    }


def test_summary_command_rejects_a_non_run_directory(tmp_path: Path) -> None:
    result = runner.invoke(app, ["operations", "summary", str(tmp_path)])
    assert result.exit_code != 0


def test_rollup_rows_against_the_chain_target(composite_run: Path, tmp_path: Path) -> None:
    bare = _bare_run(tmp_path)
    ops.write_operations_summary(_summary(composite_run), composite_run)

    rollup = build_operations_rollup(
        [composite_run, bare], target_min_s=20 * 60, target_max_s=25 * 60
    )

    full, empty = rollup.rows
    assert full.summary_source == "written"
    assert (full.items, full.elapsed_s, full.setup_s) == (2, 1860.0, 60.0)
    assert (full.chains_under_target, full.chains_within_target, full.chains_over_target) == (
        1,
        0,
        1,
    )
    assert (full.elapsed_per_item_s, full.list_cost_per_item_usd) == (930.0, 1.25)
    assert (full.peak_running, full.ceiling, full.retries) == (2, 4, 3)
    assert full.unavailable == {}
    assert empty.summary_source == "computed"
    assert empty.chain_running_p50_s is None
    assert empty.unavailable["chain_running_p50_s"].startswith("items section unavailable")
    assert empty.unavailable["list_cost_usd"] == "resource-usage-summary.md is absent"

    recomputed = build_operations_rollup(
        [composite_run], target_min_s=600, target_max_s=900, recompute=True
    )
    assert recomputed.rows[0].summary_source == "computed"
    assert recomputed.rows[0].chains_over_target == 2


def test_rollup_command_prints_md_and_json(composite_run: Path, tmp_path: Path) -> None:
    other = _composite_run(tmp_path, "run-2")
    before = _tree_state(tmp_path)

    md = runner.invoke(app, ["operations", "rollup", str(composite_run), str(other)])
    assert md.exit_code == 0, md.output
    assert "| run-1 | completed | 2 | 31.0 min | 1.0 min | 23.4 min |" in md.stdout
    assert "0 / 0 / 2" in md.stdout

    as_json = runner.invoke(
        app,
        [
            "operations",
            "rollup",
            str(composite_run),
            "--target-min-minutes",
            "15",
            "--target-max-minutes",
            "30",
            "--format",
            "json",
        ],
    )
    assert as_json.exit_code == 0, as_json.output
    row = json.loads(as_json.stdout)["rows"][0]
    assert (row["chains_within_target"], row["chains_over_target"]) == (2, 0)
    assert _tree_state(tmp_path) == before

    bad = runner.invoke(
        app,
        [
            "operations",
            "rollup",
            str(composite_run),
            "--target-min-minutes",
            "20",
            "--target-max-minutes",
            "10",
        ],
    )
    assert bad.exit_code != 0
