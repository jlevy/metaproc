"""An agent leaf inside a child process writes a result its own attempt validates.

A cohort runs one child process per rostered company, and inside each child an agent
step fans out per item. The attempt and the result for that leaf are written from
different scopes, so they must agree on how the run id is composed; when they do not,
the step succeeds and the result write raises instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from metaproc.adapters.registry import ADAPTER_REGISTRY
from metaproc.cli import app

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "nested_agent_fanout"


class _NestedFanOutMockAdapter:
    """Mock agent adapter that writes the declared profile for its item."""

    adapter_type = "nested-fanout-mock"
    short_name = "nested-fanout-mock"
    default_model = None

    def build_command(self, prompt_file, merged_config, variables):  # noqa: ARG002
        task = variables["profile_task"]
        run_dir = Path(variables["RUNS_DIR"]) / variables["RUN_ID"]
        artifact = run_dir / "profiles" / task / "profile.md"
        script = (
            "from pathlib import Path; "
            f"p = Path({str(artifact)!r}); "
            "p.parent.mkdir(parents=True, exist_ok=True); "
            f"p.write_text({f'---\\nunit: {task}\\n---\\nprofile for {task}\\n'!r})"
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


def test_an_agent_leaf_in_a_child_process_writes_a_valid_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("METAPROC_PREFLIGHT_MIN_DISK_GB", "0.1")
    monkeypatch.setitem(ADAPTER_REGISTRY, "nested-fanout-mock", _NestedFanOutMockAdapter())

    runs_dir = tmp_path / "runs"
    run_id = "nested-fanout-run"
    result = CliRunner().invoke(
        app,
        [
            "run-process",
            str(_FIXTURE_DIR / "parent.process.md"),
            "--var",
            f"RUNS_DIR={runs_dir}",
            "--var",
            f"RUN_ID={run_id}",
            "--backend",
            "local",
        ],
    )

    assert result.exit_code == 0, (
        f"nested fan-out run failed (exit={result.exit_code})\n"
        f"stdout:\n{result.stdout}\nexception: {result.exception}"
    )

    profile = runs_dir / run_id / "authoring" / "AAA" / "profiles" / "AAA" / "profile.md"
    assert profile.is_file(), f"agent leaf wrote no artifact at {profile}"

    state = runs_dir / run_id / "authoring" / "AAA" / ".state" / "tasks" / "write-profile" / "AAA"
    attempt = yaml.safe_load((state / "attempt.yaml").read_text())
    stored = yaml.safe_load((state / "result.yaml").read_text())
    assert stored["run_id"] == attempt["run_id"]
