"""The hard reaper's signal-exit work, exercised in a real process with real signals.

Each test runs a small orchestrator stand-in in a subprocess: it installs the reaper,
starts a descendant, registers exit work, and waits to be signalled. The work records
what it did in a file, so the test can see what ran, in what order, and that the
process still died by the signal with its descendant reaped.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil

_STAND_IN = """\
import subprocess, sys, threading, time
from pathlib import Path

from metaproc.runpool.kill import (
    SignalExitWork,
    install_subprocess_reaper_signal_handlers,
    register_signal_exit_work,
)

out = Path(sys.argv[1])
mode = sys.argv[2]
lock = threading.Lock()


def record(line):
    with out.joinpath("record.txt").open("a") as handle:
        handle.write(line + "\\n")


def mark(signum):
    record(f"mark {signum.name}")


def finish(signum):
    if mode == "blocks":
        lock.acquire()
    if mode == "slow":
        time.sleep(1.5)
    record(f"finish {signum.name}")


install_subprocess_reaper_signal_handlers(include_sigint=False)
unregister = register_signal_exit_work(
    SignalExitWork(mark=mark, finish=finish, timeout_s=float(sys.argv[3]))
)
if mode == "unregistered":
    unregister()
child = subprocess.Popen(["sleep", "600"])
out.joinpath("child.pid").write_text(str(child.pid))
with lock:
    out.joinpath("ready").write_text("ready")
    while True:
        time.sleep(0.05)
"""


def _start(tmp_path: Path, mode: str, *, timeout_s: float = 10.0) -> subprocess.Popen[bytes]:
    proc = subprocess.Popen(
        [sys.executable, "-c", _STAND_IN, str(tmp_path), mode, str(timeout_s)],
        stdout=subprocess.DEVNULL,
        stderr=(tmp_path / "stderr.log").open("wb"),
    )
    deadline = time.monotonic() + 30
    while not (tmp_path / "ready").exists():
        assert proc.poll() is None, (tmp_path / "stderr.log").read_text()
        assert time.monotonic() < deadline, "the stand-in never became ready"
        time.sleep(0.05)
    return proc


def _records(tmp_path: Path) -> list[str]:
    path = tmp_path / "record.txt"
    return path.read_text().splitlines() if path.exists() else []


def _child_gone(tmp_path: Path) -> bool:
    pid = int((tmp_path / "child.pid").read_text())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.05)
    return False


def _wait(proc: subprocess.Popen[bytes], timeout_s: float = 30) -> int:
    try:
        return proc.wait(timeout=timeout_s)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def test_exit_work_marks_then_finishes_before_the_process_dies_by_the_signal(
    tmp_path: Path,
) -> None:
    proc = _start(tmp_path, "normal")

    proc.send_signal(signal.SIGTERM)

    assert _wait(proc) == -signal.SIGTERM
    assert _records(tmp_path) == ["mark SIGTERM", "finish SIGTERM"]
    assert _child_gone(tmp_path)


def test_exit_work_is_bounded_when_finish_waits_on_a_lock_the_main_thread_holds(
    tmp_path: Path,
) -> None:
    """The signal lands while the main thread holds a lock the finish work needs, so the
    work can never complete; the mark is kept and the process still dies on time."""
    proc = _start(tmp_path, "blocks", timeout_s=1.0)

    started = time.monotonic()
    proc.send_signal(signal.SIGTERM)
    returncode = _wait(proc)
    elapsed = time.monotonic() - started

    assert returncode == -signal.SIGTERM
    assert elapsed < 6, f"exit took {elapsed:.1f}s against a 1s bound"
    assert _records(tmp_path) == ["mark SIGTERM"]
    assert _child_gone(tmp_path)


def test_a_repeated_signal_does_not_abort_or_repeat_exit_work(tmp_path: Path) -> None:
    proc = _start(tmp_path, "slow")

    proc.send_signal(signal.SIGTERM)
    time.sleep(0.5)
    proc.send_signal(signal.SIGTERM)

    assert _wait(proc) == -signal.SIGTERM
    assert _records(tmp_path) == ["mark SIGTERM", "finish SIGTERM"]
    assert _child_gone(tmp_path)


def test_unregistered_exit_work_does_not_run(tmp_path: Path) -> None:
    proc = _start(tmp_path, "unregistered")

    proc.send_signal(signal.SIGTERM)

    assert _wait(proc) == -signal.SIGTERM
    assert _records(tmp_path) == []
    assert _child_gone(tmp_path)
