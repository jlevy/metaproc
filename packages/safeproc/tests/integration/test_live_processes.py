"""Live tests against real processes. Bounded, local to this user's own subtree."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from safeproc._platform.base import get_provider
from safeproc.cli import main
from safeproc.identity import ProcessTarget, descendants
from safeproc.models import GuardPolicy
from safeproc.monitor import ProcessMonitor, ProducerPause, WatchOutcome, terminate_batch

pytestmark = pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="posix live tests")

TREE = "import subprocess, sys, time\nsubprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\ntime.sleep(30)\n"


def _wait_for_children(pid: int, want: int, timeout: float = 5.0) -> None:
    provider = get_provider()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(descendants(pid, provider.process_table())) >= want:
            return
        time.sleep(0.05)
    raise AssertionError("tree did not appear")


@pytest.fixture
def tree() -> Iterator[subprocess.Popen[bytes]]:
    proc = subprocess.Popen([sys.executable, "-c", TREE])
    provider = get_provider()
    try:
        _wait_for_children(proc.pid, 2)
        yield proc
    finally:
        members = [row.pid for row in descendants(proc.pid, provider.process_table())]
        for pid in [proc.pid, *members]:
            for sig in (signal.SIGCONT, signal.SIGKILL):
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.kill(pid, sig)
        proc.wait(timeout=5)


def test_provider_sees_this_process() -> None:
    provider = get_provider()
    table = {row.pid: row for row in provider.process_table()}
    me = table[os.getpid()]
    assert me.ppid == os.getppid()
    assert me.uid == os.getuid()
    sample = provider.host_sample()
    assert sample.reclaimable_gb > 0
    assert sample.pressure in {1, 2, 4}


def test_once_against_a_real_tree(tree: subprocess.Popen[bytes]) -> None:
    provider = get_provider()
    monitor = ProcessMonitor(
        ProcessTarget(pid=tree.pid), provider=provider, policy=GuardPolicy(), once=True
    )
    outcome = monitor.run()
    assert outcome in {WatchOutcome.FINISHED, WatchOutcome.DANGER}
    handle = monitor.handle
    assert handle is not None and handle.last_tree is not None
    assert handle.last_tree.procs >= 2
    assert handle.last_tree.measured


def test_pause_stops_the_root_and_resume_continues_it(tree: subprocess.Popen[bytes]) -> None:
    provider = get_provider()
    pause = ProducerPause(tree.pid, provider)
    assert pause.pause(provider.process_table()) >= 1
    time.sleep(0.2)
    state = next(r.state for r in provider.process_table() if r.pid == tree.pid)
    assert state.startswith("T") or sys.platform == "darwin"
    assert pause.resume() >= 1
    time.sleep(0.2)
    state = next(r.state for r in provider.process_table() if r.pid == tree.pid)
    assert not state.startswith("T")


def test_terminate_batch_kills_the_grandchild_too(tree: subprocess.Popen[bytes]) -> None:
    provider = get_provider()
    table = provider.process_table()
    members = [row.pid for row in descendants(tree.pid, table)]
    assert len(members) >= 2
    outcome = terminate_batch([tree.pid], provider=provider, table=table, grace_s=2.0)
    assert outcome[tree.pid] in {"term", "kill"}
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and any(provider.alive(pid) for pid in members):
        time.sleep(0.05)
    assert not any(provider.alive(pid) for pid in members)


def test_cli_once_json(tree: subprocess.Popen[bytes], capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["watch", "--pid", str(tree.pid), "--once", "--format", "json", "--no-progress"])
    assert code in {0, 3}
    report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert report["record"] == "once"
    assert report["tree"]["procs"] >= 2


def test_cli_watch_then_replay(tree: subprocess.Popen[bytes], tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "safeproc.cli",
            "watch",
            "--pid",
            str(tree.pid),
            "--journal",
            str(journal),
            "--interval",
            "0.2",
            "--no-progress",
        ]
    )
    time.sleep(1.5)
    proc.send_signal(signal.SIGINT)
    proc.wait(timeout=10)
    assert journal.exists()
    assert main(["replay", str(journal)]) == 0
