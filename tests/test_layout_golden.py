"""Golden integration test for the run-dir layout.

Runs the ``layout_smoke`` fixture end-to-end via ``run-process`` against
a mock adapter and asserts the on-disk shape:

- a single run dir at ``<RUNS_DIR>/<RUN_ID>/`` (no inferred parent)
- ``<run>/.state/`` and ``<run>/.logs/`` as the only engine-bookkeeping
  branches
- per-step pool state under ``<run>/.state/steps/<step_id>/``
- per-task state under ``<run>/.state/tasks/<step_id>/<item_key>/``
  (non-fan-out steps write status.yaml directly under
  ``<run>/.state/tasks/<step_id>/``)
- artifacts in the user-templated tree, with no ``.state/`` siblings

Negative assertions guard against engine bookkeeping leaking into the
artifact tree at ``<run>/<step_id>/.state/`` or
``<artifact_parent>/.state/``.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel, Field
from typer.testing import CliRunner

from metaproc.adapters.registry import ADAPTER_REGISTRY
from metaproc.cli import app
from metaproc.io.frontmatter import ENVELOPE_MAP
from metaproc.io.state_io import read_status_at
from metaproc.models.runtime import MapItem
from metaproc.paths import (
    LOGS_DIR,
    POOL_STATUS_FILE,
    RUN_LAYOUT_VERSION,
    STATE_DIR,
    STATUS_FILE,
    runpool_step_events,
    step_state_dir,
    task_state_dir,
)


class _TickersFrontmatter(BaseModel):
    date: str = ""
    process: str = ""
    items: list[MapItem] = Field(default_factory=list)
    model_config = {"extra": "allow"}


class _TickersEnvelope(BaseModel):
    items: _TickersFrontmatter


_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "layout_smoke"


class _LayoutSmokeMockAdapter:
    """Mock agent adapter that writes the declared artifact for the item."""

    adapter_type = "layout-smoke-mock"
    short_name = "layout-smoke-mock"
    default_model = None

    def build_command(self, prompt_file, merged_config, variables):  # noqa: ARG002
        item = variables["item"]
        run_dir = Path(variables["RUNS_DIR"]) / variables["RUN_ID"]
        artifact = run_dir / "artifacts" / item / "out.md"
        script = (
            "from pathlib import Path; "
            f"p = Path({str(artifact)!r}); "
            "p.parent.mkdir(parents=True, exist_ok=True); "
            f"p.write_text({f'---\\nticker: {item}\\n---\\nartifact for {item}\\n'!r})"
        )
        return [sys.executable, "-c", script]

    def prepare_env(self, env, merged_config):  # noqa: ARG002
        return env

    def working_directory(self, merged_config):  # noqa: ARG002
        return None

    def parse_result_event(self, line):  # noqa: ARG002
        return None

    def check_auth(self):
        raise NotImplementedError

    def auth_info(self):
        return ""


@pytest.fixture(scope="module")
def smoke_run(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Run the layout-smoke fixture once and return the run dir."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("METAPROC_PREFLIGHT_MIN_DISK_GB", "0.1")
        monkeypatch.setitem(ADAPTER_REGISTRY, "layout-smoke-mock", _LayoutSmokeMockAdapter())
        monkeypatch.setitem(ENVELOPE_MAP, "items", _TickersEnvelope)

        runs_dir = tmp_path_factory.mktemp("layout-smoke") / "runs"
        run_id = "layout-smoke-run"
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "run-process",
                str(_FIXTURE_DIR / "layout-smoke.process.md"),
                "--var",
                f"RUNS_DIR={runs_dir}",
                "--var",
                f"RUN_ID={run_id}",
                "--backend",
                "local",
            ],
        )
        assert result.exit_code == 0, (
            f"layout-smoke run failed (exit={result.exit_code})\n"
            f"stdout:\n{result.stdout}\n"
            f"exception: {result.exception}"
        )
        yield runs_dir / run_id


def test_artifact_tree_has_no_state_or_logs_siblings(smoke_run: Path) -> None:
    """Artifacts in the user tree must NOT be co-mingled with engine state/logs."""
    artifacts_dir = smoke_run / "artifacts"
    assert artifacts_dir.is_dir()

    for item in ("AAA", "BBB"):
        ticker_dir = artifacts_dir / item
        assert ticker_dir.is_dir(), f"missing artifact dir: {ticker_dir}"
        assert (ticker_dir / "out.md").exists(), f"missing artifact file: {ticker_dir}"
        # No engine bookkeeping inside the artifact tree.
        assert not (ticker_dir / STATE_DIR).exists(), (
            f"per-task .state/ leaked into artifact dir: {ticker_dir / STATE_DIR}"
        )
        assert not (ticker_dir / LOGS_DIR).exists(), (
            f"per-task .logs/ leaked into artifact dir: {ticker_dir / LOGS_DIR}"
        )


def test_run_dir_has_three_top_level_branches(smoke_run: Path) -> None:
    """A run dir exposes engine branches, process artifacts, and terminal reports."""
    expected_top_level = {
        ".state",
        ".logs",
        "artifacts",
        "items.md",
        "summary.md",
        "resources.json",
        "resource-usage-summary.md",
    }
    actual = {entry.name for entry in smoke_run.iterdir()}
    assert actual == expected_top_level, (
        f"unexpected top-level entries in {smoke_run}: "
        f"extra={actual - expected_top_level} missing={expected_top_level - actual}"
    )


def test_per_step_state_lives_under_state_steps(smoke_run: Path) -> None:
    """Per-step fan-out pool state must live at <run>/.state/steps/<step_id>/."""
    expected_pool_status = step_state_dir(smoke_run, "write-artifact") / POOL_STATUS_FILE
    assert expected_pool_status.exists(), (
        f"per-step pool status not at expected path: {expected_pool_status}"
    )

    legacy_path = smoke_run / "write-artifact" / STATE_DIR / POOL_STATUS_FILE
    assert not legacy_path.exists(), f"per-step pool state regressed to legacy path: {legacy_path}"


def test_per_step_logs_live_under_logs_runpool_steps(smoke_run: Path) -> None:
    """Per-step pool event logs must live at <run>/.logs/runpool/steps/<step_id>/."""
    events_path = runpool_step_events(smoke_run, "write-artifact")
    assert events_path.exists(), f"per-step events not at expected path: {events_path}"

    old_step_events = smoke_run / LOGS_DIR / "steps" / "write-artifact" / "runpool-events.jsonl"
    assert not old_step_events.exists(), (
        f"per-step logs regressed to old V1 path: {old_step_events}"
    )

    legacy_events = smoke_run / "write-artifact" / LOGS_DIR / "runpool-events.jsonl"
    assert not legacy_events.exists(), f"per-step logs regressed to legacy path: {legacy_events}"


def test_per_task_logs_live_under_logs_tasks(smoke_run: Path) -> None:
    """Per-task agent session logs must live at <run>/.logs/tasks/<step_id>/<item_key>/."""
    for item in ("AAA", "BBB"):
        task_logs_root = smoke_run / LOGS_DIR / "tasks" / "write-artifact" / item
        assert task_logs_root.is_dir(), f"per-task logs dir not at expected path: {task_logs_root}"
        jsonl_files = list(task_logs_root.glob("write-artifact_*.jsonl"))
        assert jsonl_files, f"no per-task session log for {item}: {task_logs_root}"

    # Per-task logs must NOT land flat at <run>/.logs/.
    flat_jsonls = list((smoke_run / LOGS_DIR).glob("write-artifact_*.jsonl"))
    assert not flat_jsonls, f"per-task logs regressed to flat <run>/.logs/ location: {flat_jsonls}"


def test_per_task_state_lives_under_state_tasks(smoke_run: Path) -> None:
    """Per-task state must live at <run>/.state/tasks/<step_id>/<item_key>/."""
    for item in ("AAA", "BBB"):
        per_task_state = task_state_dir(smoke_run, "write-artifact", item)
        status_file = per_task_state / STATUS_FILE
        assert status_file.exists(), f"per-task status not at expected path: {status_file}"
        record = read_status_at(per_task_state)
        assert record is not None
        assert record.state == "completed"
        assert record.step_id == "write-artifact"


def test_non_fan_out_state_at_step_level(smoke_run: Path) -> None:
    """Non-fan-out steps put their state directly under <run>/.state/tasks/<step_id>/."""
    for step_id in ("scaffold", "summarize"):
        scalar_state = smoke_run / STATE_DIR / "tasks" / step_id
        status_file = scalar_state / STATUS_FILE
        assert status_file.exists(), f"non-fan-out step {step_id} status missing at {status_file}"
        record = read_status_at(scalar_state)
        assert record is not None
        assert record.state == "completed"


def test_run_level_state_files_are_present(smoke_run: Path) -> None:
    """<run>/.state/ should hold run-level bookkeeping (run-config, process-status)."""
    run_state = smoke_run / STATE_DIR

    assert (run_state / "run-config.yaml").exists(), "run-config.yaml missing"
    assert (run_state / "process-status.yaml").exists(), "process-status.yaml missing"
    assert f"metaproc_layout: {RUN_LAYOUT_VERSION}" in (run_state / "run-config.yaml").read_text()

    # The run-level .state/ has steps/ and tasks/ namespaces, NOT raw status.yaml.
    assert not (run_state / STATUS_FILE).exists(), (
        "an unscoped status.yaml at the run-level .state/ root indicates step state "
        "is escaping the per-task path"
    )
    assert (run_state / "steps").is_dir(), "expected .state/steps/ namespace"
    assert (run_state / "tasks").is_dir(), "expected .state/tasks/ namespace"


def test_no_phantom_inferred_parent_dir(smoke_run: Path) -> None:
    """No phantom ``<process_name>/`` subdir between RUN_ID and the artifact tree.

    Pre-refactor, ``compute_run_dir`` walked up to the common ancestor of all
    declared output paths, which created an extra ``<process-name>/`` layer
    (e.g. ``analysis-research/``) wrapping everything in the run dir. The new
    ``compute_run_dir`` returns ``RUNS_DIR/RUN_ID`` unchanged.
    """
    # If a phantom parent had been inserted, .state/ would live one level
    # deeper instead of at the run dir root.
    assert (smoke_run / STATE_DIR).is_dir()
    assert (smoke_run / LOGS_DIR).is_dir()

    # No top-level subdir matching the process name wrapping everything.
    assert not (smoke_run / "layout-smoke").exists()


def test_state_tree_snapshot_matches_expected_shape(smoke_run: Path) -> None:
    """Snapshot of every state file under <run>/.state/ — locks the layout in place."""
    state_root = smoke_run / STATE_DIR
    state_files = sorted(
        re.sub(
            r"/attempts/att-[^/]+/attempt\.yaml$",
            "/attempts/<attempt-id>/attempt.yaml",
            str(path.relative_to(state_root)),
        )
        for path in state_root.rglob("*")
        if path.is_file()
    )

    expected = {
        # Run-level
        "process-status.yaml",
        "run-config.yaml",
        "schemas/resource-usage-summary.v1.schema.yaml",
        # Per-step (fan-out runner pool)
        "steps/write-artifact/runpool-status.yaml",
        "steps/write-artifact/scale-state.yaml",
        # Per-task — fan-out items each get their own subdir
        "tasks/write-artifact/AAA/attempt.yaml",
        "tasks/write-artifact/AAA/attempts/<attempt-id>/attempt.yaml",
        "tasks/write-artifact/AAA/result.yaml",
        "tasks/write-artifact/AAA/status.yaml",
        "tasks/write-artifact/BBB/attempt.yaml",
        "tasks/write-artifact/BBB/attempts/<attempt-id>/attempt.yaml",
        "tasks/write-artifact/BBB/result.yaml",
        "tasks/write-artifact/BBB/status.yaml",
        # Per-task — non-fan-out steps write status.yaml directly under
        # tasks/<step_id>/
        "tasks/scaffold/result.yaml",
        "tasks/scaffold/status.yaml",
        "tasks/scaffold/attempts/<attempt-id>/attempt.yaml",
        "tasks/summarize/result.yaml",
        "tasks/summarize/status.yaml",
        "tasks/summarize/attempts/<attempt-id>/attempt.yaml",
    }

    actual = set(state_files)
    missing = expected - actual
    extra = actual - expected
    assert not missing, f"missing expected state files: {sorted(missing)}"
    assert not extra, f"unexpected state files: {sorted(extra)}"
