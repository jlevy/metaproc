"""Tests for `metaproc resource-report` CLI (B12, spec §8.6)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.commands.run_process import _write_run_config
from metaproc.errors import CLIError
from metaproc.models.resource_snapshot import ResourceRunSnapshot, ResourceTopologyNode

runner = CliRunner()


_PARENT_PROCESS = """\
---
process:
  name: parent
  deps:
    child_proc:
      path: child/test.process.md
      role: process
      as: path
  steps:
    - id: run_child
      mode: composite
      uses: deps.child_proc
      with:
        CUTOFF_DATE: "{{CUTOFF_DATE}}"
---

Parent body.
"""

_CHILD_PROCESS = """\
---
process:
  name: child
  steps:
    - id: leaf
      mode: code
      handler: metaproc.code_steps.scaffold
---

Child body.
"""


def _write(tmp_path: Path, rel: str, content: str) -> Path:
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


def _setup_run(tmp_path: Path) -> tuple[Path, Path]:
    """Build a small composite spec + run dir; return (spec_path, run_dir)."""
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    _write(tmp_path, "parent/child/test.process.md", _CHILD_PROCESS)
    run_dir = tmp_path / "runs" / "2026-04-21"
    run_dir.mkdir(parents=True)
    return parent, run_dir


def test_first_invocation_builds_and_persists_resources_json(tmp_path: Path) -> None:
    spec_path, run_dir = _setup_run(tmp_path)

    result = runner.invoke(
        app,
        [
            "resource-report",
            str(run_dir),
            "--spec",
            str(spec_path),
            "--param",
            "CUTOFF_DATE=2026-04-21",
        ],
    )
    assert result.exit_code == 0, result.output
    cached = run_dir / "resources.json"
    assert cached.exists()
    payload = json.loads(cached.read_text())
    assert payload["schema"] == "metaproc.resources/v2"
    assert "[run]" in result.output


def test_subsequent_invocation_reads_cached_resources_json(tmp_path: Path) -> None:
    spec_path, run_dir = _setup_run(tmp_path)
    runner.invoke(
        app,
        [
            "resource-report",
            str(run_dir),
            "--spec",
            str(spec_path),
            "--param",
            "CUTOFF_DATE=2026-04-21",
        ],
    )

    # Subsequent call without --refresh should not need --spec.
    result = runner.invoke(app, ["resource-report", str(run_dir)])
    assert result.exit_code == 0, result.output
    assert "schema=" in result.output
    assert "actual_usd: unmeasured" in result.output
    assert "list_estimate_usd:" in result.output


def test_cached_v1_document_remains_readable(tmp_path: Path) -> None:
    _spec_path, run_dir = _setup_run(tmp_path)
    (run_dir / "resources.json").write_text(
        json.dumps(
            {
                "schema": "metaproc.resources/v1",
                "run_id": "historical",
                "generated_at": "2026-04-21T00:00:00Z",
                "source_events_path": ".logs/resource-events.jsonl",
                "hierarchy_root": {
                    "node_type": "run",
                    "node_id": "historical",
                    "label": "historical",
                },
            }
        )
    )

    result = runner.invoke(app, ["resource-report", str(run_dir), "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["schema"] == "metaproc.resources/v1"


def test_missing_spec_on_first_build_returns_clean_error(tmp_path: Path) -> None:
    _spec_path, run_dir = _setup_run(tmp_path)
    result = runner.invoke(app, ["resource-report", str(run_dir)])
    assert result.exit_code != 0

    assert isinstance(result.exception, CLIError)
    assert "spec" in str(result.exception).lower()


def test_json_flag_emits_valid_resources_document(tmp_path: Path) -> None:
    spec_path, run_dir = _setup_run(tmp_path)
    result = runner.invoke(
        app,
        [
            "resource-report",
            str(run_dir),
            "--spec",
            str(spec_path),
            "--param",
            "CUTOFF_DATE=2026-04-21",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema"] == "metaproc.resources/v2"
    assert payload["run_id"] == run_dir.name
    assert payload["hierarchy_root"]["node_type"] == "run"


def test_refresh_rebuilds_even_when_cache_present(tmp_path: Path) -> None:
    spec_path, run_dir = _setup_run(tmp_path)
    cached = run_dir / "resources.json"
    cached.write_text("{}")  # garbage cache

    result = runner.invoke(
        app,
        [
            "resource-report",
            str(run_dir),
            "--spec",
            str(spec_path),
            "--param",
            "CUTOFF_DATE=2026-04-21",
            "--refresh",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(cached.read_text())
    assert payload.get("schema") == "metaproc.resources/v2"


def test_does_not_shadow_existing_resources_command(tmp_path: Path) -> None:
    """`metaproc resources` (host context) and `resource-report` (run rollup) coexist."""
    result = runner.invoke(app, ["resources", "--json"])
    assert result.exit_code == 0, result.output
    # Existing host-context output is JSON; should parse cleanly.
    json.loads(result.output)


def test_snapshot_refresh_rejects_active_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, run_dir = _setup_run(tmp_path)
    _write_run_config(
        run_dir,
        process_name="parent",
        process_path=spec_path,
        run_id="2026-04-21",
        variables={},
        backend="local",
        variant=None,
        resource_snapshot=ResourceRunSnapshot(
            hierarchy=ResourceTopologyNode(
                node_type="run",
                node_id="parent/2026-04-21",
                label="parent/2026-04-21",
            )
        ),
    )
    monkeypatch.setattr(
        "metaproc.engine.run_status.is_orchestrator_alive",
        lambda _run_dir: True,
    )

    result = runner.invoke(app, ["resource-report", str(run_dir), "--refresh"])

    assert isinstance(result.exception, CLIError)
    assert "active" in str(result.exception)
    assert not (run_dir / "resources.json").exists()


def test_snapshot_recovery_preserves_completed_process_outcome(tmp_path: Path) -> None:
    spec_path, run_dir = _setup_run(tmp_path)
    _write_run_config(
        run_dir,
        process_name="parent",
        process_path=spec_path,
        run_id="2026-04-21",
        variables={},
        backend="local",
        variant=None,
        resource_snapshot=ResourceRunSnapshot(
            hierarchy=ResourceTopologyNode(
                node_type="run",
                node_id="parent/2026-04-21",
                label="parent/2026-04-21",
            )
        ),
    )
    (run_dir / ".state" / "process-status.yaml").write_text("state: completed\n")

    result = runner.invoke(app, ["resource-report", str(run_dir), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["finalization"]["state"] == "completed"
