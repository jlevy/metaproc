"""A run's usage summaries survive every orchestrator exit, a hard-stop signal included.

Each test drives a real ``metaproc run-process`` subprocess and sends it real signals, so
the hard descendant reaper, the signal-exit work and status recovery run as they do for
an operator.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from metaproc.engine.operations_summary import read_operations_summary
from metaproc.io import fmf_read_frontmatter
from metaproc.models.resource_budget import FinalizationState
from metaproc.models.resource_summary import ResourceUsageSummary
from metaproc.models.resources import ResourcesDocument, read_resources_document

_ROOT = Path(__file__).resolve().parents[1]

# Step events record elapsed time rounded to 0.1 s, so the completed step sleeps long
# enough for its wall time to be measured on a fast host.
_PROCESS = """\
---
process:
  name: signal-exit
  steps:
    - id: quick
      mode: code
      command: "sh -c 'sleep 0.2 && echo quick > {{run.dir}}/quick.txt'"
      outputs:
        out:
          path: "{{run.dir}}/quick.txt"
          kind: file
    - id: slow
      mode: code
      command: "sh -c 'echo $$ > {{run.dir}}/slow.pid && exec sleep 600'"
      needs: [quick]
      outputs:
        out:
          path: "{{run.dir}}/slow.txt"
          kind: file
---
# Signal exit
"""

_FAILING_PROCESS = """\
---
process:
  name: signal-exit-failure
  steps:
    - id: quick
      mode: code
      command: "sh -c 'sleep 0.2 && echo quick > {{run.dir}}/quick.txt'"
      outputs:
        out:
          path: "{{run.dir}}/quick.txt"
          kind: file
    - id: broken
      mode: code
      command: "sh -c 'exit 3'"
      needs: [quick]
---
# Failing step
"""


def _launch(
    process_path: Path, runs_dir: Path, run_id: str, log_name: str
) -> subprocess.Popen[bytes]:
    log_path = runs_dir.parent / f"{run_id}-{log_name}.log"
    with log_path.open("wb") as log_file:
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "metaproc",
                "run-process",
                str(process_path),
                "--var",
                f"RUNS_DIR={runs_dir}",
                "--var",
                f"RUN_ID={run_id}",
            ],
            cwd=_ROOT,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )


def _slow_step_pid(run_dir: Path, proc: subprocess.Popen[bytes], *, timeout_s: float = 45) -> int:
    """Wait until the slow step's command is running and return its PID."""
    pid_file = run_dir / "slow.pid"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if pid_file.is_file() and pid_file.read_text().strip():
            return int(pid_file.read_text().strip())
        assert proc.poll() is None, f"run-process exited early with {proc.returncode}"
        time.sleep(0.1)
    raise AssertionError(f"the slow step never started under {run_dir}")


def _gone(pid: int, *, timeout_s: float = 10) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.1)
    return False


def _resource_summary(run_dir: Path) -> ResourceUsageSummary:
    path = run_dir / "resource-usage-summary.md"
    assert path.is_file(), f"no resource-usage-summary.md under {run_dir}"
    metadata = fmf_read_frontmatter(path)
    assert metadata is not None
    return ResourceUsageSummary.model_validate(metadata["resource_usage"])


def _stop(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=10)


def test_sigterm_mid_run_writes_both_summaries_and_still_reaps_descendants(
    tmp_path: Path,
) -> None:
    process_path = tmp_path / "signal-exit.process.md"
    process_path.write_text(_PROCESS)
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "run-1"
    proc = _launch(process_path, runs_dir, "run-1", "launch")
    try:
        step_pid = _slow_step_pid(run_dir, proc)

        proc.send_signal(signal.SIGTERM)
        returncode = proc.wait(timeout=60)
    finally:
        _stop(proc)

    # The orchestrator still dies by the signal, and the reaper still kills the step.
    assert returncode == -signal.SIGTERM
    assert _gone(step_pid), f"slow step PID {step_pid} survived the orchestrator"

    summary = _resource_summary(run_dir)
    assert summary.finalization.state is FinalizationState.CANCELLED
    assert summary.finalization.trigger == "terminal"
    assert summary.finalization.recovered is False
    assert summary.finalization.terminal_error_type == "SIGTERM"
    assert summary.finalization.incomplete is False
    # A real rollup: the completed step's time is in the totals.
    assert summary.totals.wall_time_s is not None and summary.totals.wall_time_s > 0

    document = read_resources_document(run_dir / "resources.json")
    assert isinstance(document, ResourcesDocument)
    assert document.finalization is not None
    assert document.finalization.state is FinalizationState.CANCELLED

    operations = read_operations_summary(run_dir).summary
    assert operations is not None and operations.run is not None
    assert operations.trigger == "finalization"
    assert (operations.run.state, operations.run.state_source) == ("cancelled", "finalization")


_WITHOUT_CORE_DUMPS = """\
import resource, sys

import metaproc.cli

# SIGQUIT's default action dumps core, which this test has no use for.
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
sys.argv = ["metaproc", "run-process", *sys.argv[1:]]
metaproc.cli.main()
"""


def _kill_if_alive(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


@pytest.mark.parametrize("signum", [signal.SIGHUP, signal.SIGQUIT], ids=lambda s: s.name)
def test_sighup_and_sigquit_end_a_run_as_sigterm_does(
    tmp_path: Path, signum: signal.Signals
) -> None:
    """A dropped terminal's SIGHUP, or a SIGQUIT, reaps the steps and writes both
    summaries before the orchestrator dies by that signal."""
    process_path = tmp_path / "signal-exit.process.md"
    process_path.write_text(_PROCESS)
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "run-1"
    with (tmp_path / "launch.log").open("wb") as log_file:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _WITHOUT_CORE_DUMPS,
                str(process_path),
                "--var",
                f"RUNS_DIR={runs_dir}",
                "--var",
                "RUN_ID=run-1",
            ],
            cwd=_ROOT,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    step_pid: int | None = None
    try:
        step_pid = _slow_step_pid(run_dir, proc)
        proc.send_signal(signum)
        returncode = proc.wait(timeout=60)
        reaped = _gone(step_pid)
    finally:
        _stop(proc)
        if step_pid is not None:
            _kill_if_alive(step_pid)

    assert returncode == -signum
    assert reaped, f"slow step PID {step_pid} survived the orchestrator"
    summary = _resource_summary(run_dir)
    assert summary.finalization.state is FinalizationState.CANCELLED
    assert summary.finalization.trigger == "terminal"
    assert summary.finalization.terminal_error_type == signum.name
    assert summary.finalization.incomplete is False
    operations = read_operations_summary(run_dir).summary
    assert operations is not None and operations.run is not None
    assert (operations.trigger, operations.run.state) == ("finalization", "cancelled")


_STALLED_ROLLUP = """\
import functools, sys, time

import metaproc.cli
from metaproc.commands import run_process
from metaproc.runpool import kill


def never_finishes(*_args, **_kwargs):
    time.sleep(600)


# The rollup cannot finish, and the exit work is bounded at one second.
run_process.finalize_run_resources = never_finishes
run_process.SignalExitWork = functools.partial(kill.SignalExitWork, timeout_s=1.0)
sys.argv = ["metaproc", "run-process", *sys.argv[1:]]
metaproc.cli.main()
"""


def test_sigterm_leaves_an_incomplete_summary_when_the_rollup_cannot_finish(
    tmp_path: Path,
) -> None:
    process_path = tmp_path / "signal-exit.process.md"
    process_path.write_text(_PROCESS)
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "run-1"
    with (tmp_path / "launch.log").open("wb") as log_file:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _STALLED_ROLLUP,
                str(process_path),
                "--var",
                f"RUNS_DIR={runs_dir}",
                "--var",
                "RUN_ID=run-1",
            ],
            cwd=_ROOT,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    try:
        step_pid = _slow_step_pid(run_dir, proc)
        started = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        returncode = proc.wait(timeout=60)
        elapsed = time.monotonic() - started
    finally:
        _stop(proc)

    assert returncode == -signal.SIGTERM
    assert elapsed < 10, f"exit took {elapsed:.1f}s against a 1s bound"
    assert _gone(step_pid)
    marked = _resource_summary(run_dir)
    assert marked.finalization.incomplete is True
    assert marked.finalization.state is FinalizationState.CANCELLED
    assert marked.finalization.terminal_error_type == "SIGTERM"
    assert marked.totals.wall_time_s is None
    assert (
        "before its resource rollup finished" in (run_dir / "resource-usage-summary.md").read_text()
    )

    status = subprocess.run(
        [sys.executable, "-m", "metaproc", "status", str(run_dir), "--no-system"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert status.returncode == 0, status.stdout + status.stderr
    rebuilt = _resource_summary(run_dir)
    assert rebuilt.finalization.incomplete is False
    assert (rebuilt.finalization.trigger, rebuilt.finalization.recovered) == ("status", True)
    assert rebuilt.finalization.state is FinalizationState.CANCELLED
    assert rebuilt.totals.wall_time_s is not None and rebuilt.totals.wall_time_s > 0


_QUICK_PROCESS = """\
---
process:
  name: signal-exit-quick
  steps:
    - id: quick
      mode: code
      command: "sh -c 'echo quick > {{run.dir}}/quick.txt'"
      outputs:
        out:
          path: "{{run.dir}}/quick.txt"
          kind: file
---
# Quick
"""

_STALLED_OPERATIONS_SUMMARY = """\
import functools, sys, time
from pathlib import Path

import metaproc.cli
from metaproc.commands import run_process
from metaproc.runpool import kill

out = Path(sys.argv[1])
real_finalize_run_resources = run_process.finalize_run_resources


def counted_rollup(*args, **kwargs):
    with (out / "rollups.txt").open("a") as handle:
        handle.write(kwargs["trigger"] + "\\n")
    return real_finalize_run_resources(*args, **kwargs)


def stalled_operations_summary(*_args, **_kwargs):
    (out / "operations.started").write_text("started")
    time.sleep(600)


# The resource rollup runs as usual; the operations summary after it never finishes.
run_process.finalize_run_resources = counted_rollup
run_process.finalize_operations_summary = stalled_operations_summary
run_process.SignalExitWork = functools.partial(kill.SignalExitWork, timeout_s=1.0)
sys.argv = ["metaproc", "run-process", *sys.argv[2:]]
metaproc.cli.main()
"""


def test_a_signal_after_the_resource_rollup_keeps_it(tmp_path: Path) -> None:
    """The body completed and its resource rollup is written; a SIGTERM while the
    operations summary is being written neither marks that rollup incomplete nor runs it
    again, and the run keeps the body's outcome."""
    process_path = tmp_path / "quick.process.md"
    process_path.write_text(_QUICK_PROCESS)
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "run-1"
    with (tmp_path / "launch.log").open("wb") as log_file:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _STALLED_OPERATIONS_SUMMARY,
                str(tmp_path),
                str(process_path),
                "--var",
                f"RUNS_DIR={runs_dir}",
                "--var",
                "RUN_ID=run-1",
            ],
            cwd=_ROOT,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + 45
        while not (tmp_path / "operations.started").exists():
            assert proc.poll() is None, (tmp_path / "launch.log").read_text()
            assert time.monotonic() < deadline, "the operations summary never started"
            time.sleep(0.1)
        proc.send_signal(signal.SIGTERM)
        returncode = proc.wait(timeout=60)
    finally:
        _stop(proc)

    assert returncode == -signal.SIGTERM
    assert (tmp_path / "rollups.txt").read_text().splitlines() == ["terminal"]
    summary = _resource_summary(run_dir)
    assert summary.finalization.incomplete is False
    assert summary.finalization.state is FinalizationState.COMPLETED
    assert summary.finalization.terminal_error_type is None


def test_a_failing_step_writes_both_summaries(tmp_path: Path) -> None:
    process_path = tmp_path / "failing.process.md"
    process_path.write_text(_FAILING_PROCESS)
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "run-1"
    proc = _launch(process_path, runs_dir, "run-1", "launch")
    try:
        returncode = proc.wait(timeout=60)
    finally:
        _stop(proc)

    assert returncode not in (0, -signal.SIGTERM)
    summary = _resource_summary(run_dir)
    assert summary.finalization.state is FinalizationState.FAILED
    assert summary.finalization.trigger == "terminal"
    assert summary.totals.wall_time_s is not None and summary.totals.wall_time_s > 0
    operations = read_operations_summary(run_dir).summary
    assert operations is not None and operations.run is not None
    assert (operations.run.state, operations.run.state_source) == ("failed", "finalization")


def test_status_backfills_a_resumed_run_whose_earlier_orchestrator_was_killed(
    tmp_path: Path,
) -> None:
    """SIGKILL cannot be handled, so the killed attempt leaves no summary. A resume makes
    the run active again; status must still write the killed attempt's summary, and the
    resumed orchestrator's own SIGTERM exit then replaces it with its terminal one."""
    process_path = tmp_path / "signal-exit.process.md"
    process_path.write_text(_PROCESS)
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "run-1"

    first = _launch(process_path, runs_dir, "run-1", "launch")
    try:
        orphan_pid = _slow_step_pid(run_dir, first)
        first.send_signal(signal.SIGKILL)
        assert first.wait(timeout=30) == -signal.SIGKILL
    finally:
        _stop(first)
    # Nothing reaps a SIGKILLed orchestrator's step; clean it up as an operator would.
    os.kill(orphan_pid, signal.SIGKILL)
    assert _gone(orphan_pid)
    assert not (run_dir / "resource-usage-summary.md").exists()
    (run_dir / "slow.pid").unlink()

    resumed = _launch(process_path, runs_dir, "run-1", "resume")
    try:
        step_pid = _slow_step_pid(run_dir, resumed)
        status = subprocess.run(
            [sys.executable, "-m", "metaproc", "status", str(run_dir), "--no-system"],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert status.returncode == 0, status.stdout + status.stderr
        assert resumed.poll() is None, "the resumed run must still be active"

        backfilled = _resource_summary(run_dir)
        assert backfilled.finalization.trigger == "status"
        assert backfilled.finalization.recovered is True
        assert backfilled.totals.wall_time_s is not None
        operations = read_operations_summary(run_dir).summary
        assert operations is not None and operations.trigger == "status"

        resumed.send_signal(signal.SIGTERM)
        returncode = resumed.wait(timeout=60)
    finally:
        _stop(resumed)

    assert returncode == -signal.SIGTERM
    assert _gone(step_pid)
    terminal = _resource_summary(run_dir)
    assert terminal.finalization.trigger == "terminal"
    assert terminal.finalization.state is FinalizationState.CANCELLED
    assert terminal.finalization.terminal_error_type == "SIGTERM"
    operations = read_operations_summary(run_dir).summary
    assert operations is not None and operations.trigger == "finalization"
