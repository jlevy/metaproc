"""Runtime step resource sampling tests."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from typing import Self, cast
from unittest.mock import MagicMock

import psutil
import pytest

from metaproc.engine import resource_sampling as resource_sampling_module
from metaproc.engine.resource_sampling import sample_step_resources
from metaproc.logutil.resource_events import read_events
from metaproc.models.resources import SampleEvent
from metaproc.paths import LOGS_DIR, RESOURCE_EVENTS_FILE, run_config_file


class _FakeProcess:
    def __init__(
        self,
        pid: int,
        rss: int,
        children: list[_FakeProcess] | None = None,
    ) -> None:
        self.pid = pid
        self.rss = rss
        self.direct_children = children or []

    def children(self, *, recursive: bool = False) -> list[_FakeProcess]:
        if not recursive:
            return list(self.direct_children)
        descendants: list[_FakeProcess] = []
        pending = list(self.direct_children)
        while pending:
            child = pending.pop(0)
            descendants.append(child)
            pending.extend(child.direct_children)
        return descendants

    def cpu_percent(self, interval: float | None = None) -> float:
        del interval
        return 0.0

    def memory_info(self) -> SimpleNamespace:
        return SimpleNamespace(rss=self.rss)


def test_composite_step_samples_land_in_root_ledger(tmp_path: Path) -> None:
    root_run_dir = tmp_path / "run"
    config_path = run_config_file(root_run_dir)
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{}")
    child_run_dir = root_run_dir / "outer" / "inner"
    child_run_dir.mkdir(parents=True)

    with sample_step_resources(
        run_dir=child_run_dir,
        run_id="process/run-1/outer/inner",
        step_node_id="analyze",
    ):
        pass

    event_path = root_run_dir / LOGS_DIR / RESOURCE_EVENTS_FILE
    samples = [event for event in read_events(event_path) if isinstance(event, SampleEvent)]
    assert samples
    assert {event.hierarchy.run_id for event in samples} == {"process/run-1"}
    assert {event.hierarchy.step_node_id for event in samples} == {"outer::inner::analyze"}
    assert not (child_run_dir / LOGS_DIR / RESOURCE_EVENTS_FILE).exists()


def test_in_process_sampling_excludes_preexisting_child_subtrees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sibling_grandchild = _FakeProcess(pid=3, rss=2_000)
    sibling = _FakeProcess(pid=2, rss=1_000, children=[sibling_grandchild])
    root = _FakeProcess(pid=1, rss=100, children=[sibling])
    monkeypatch.setattr(psutil, "Process", lambda pid=None: root)

    with sample_step_resources(
        run_dir=tmp_path / "run",
        run_id="process/run-1",
        step_node_id="handler",
    ) as sampler:
        sibling.direct_children.append(_FakeProcess(pid=6, rss=4_000))
        handler_grandchild = _FakeProcess(pid=5, rss=30)
        root.direct_children.append(_FakeProcess(pid=4, rss=20, children=[handler_grandchild]))

    assert [rss for _ts, _cpu, rss in sampler.stats.samples] == [100, 150]


def test_sampling_continues_when_event_log_cannot_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingLogger:
        def __init__(self, path: Path) -> None:
            del path

        def __enter__(self) -> Self:
            raise PermissionError("telemetry path is read-only")

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(resource_sampling_module, "ResourceEventLogger", _FailingLogger)

    body_executed = False
    with sample_step_resources(
        run_dir=tmp_path / "run",
        run_id="process/run-1",
        step_node_id="handler",
    ) as sampler:
        body_executed = True

    assert body_executed
    assert sampler.stats.sample_count >= 1


def test_windows_cleanup_error_does_not_replace_command_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = MagicMock(pid=1234)
    process.poll.return_value = None
    process.wait.side_effect = subprocess.TimeoutExpired("command", 1)
    process.kill.side_effect = PermissionError("access denied")
    monkeypatch.setattr(sys, "platform", "win32")

    resource_sampling_module._terminate_process_tree(
        cast("subprocess.Popen[str]", process),
    )

    process.kill.assert_called_once_with()


def test_completed_command_does_not_signal_a_recycled_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = MagicMock(pid=1234)
    process.poll.return_value = 0
    recycled = MagicMock(pid=1235, info={"pid": 1235, "create_time": 30.0})
    monkeypatch.setattr(psutil, "process_iter", lambda _attrs: [recycled], raising=False)
    monkeypatch.setattr(os, "getpgid", lambda _pid: 1234)
    killpg = MagicMock()
    monkeypatch.setattr(os, "killpg", killpg)

    resource_sampling_module._terminate_process_tree(
        cast("subprocess.Popen[str]", process),
        leader_create_time=10.0,
        ownership_observed_until=20.0,
    )

    killpg.assert_not_called()


def test_completed_command_rejects_a_recycled_group_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = MagicMock(pid=1234)
    process.poll.return_value = 0
    recycled_leader = MagicMock(pid=1234, info={"pid": 1234, "create_time": 15.0})
    recycled_child = MagicMock(pid=1235, info={"pid": 1235, "create_time": 16.0})
    monkeypatch.setattr(psutil, "process_iter", lambda _attrs: [recycled_leader, recycled_child])
    monkeypatch.setattr(os, "getpgid", lambda _pid: 1234)
    monkeypatch.setattr(
        resource_sampling_module,
        "_process_create_time",
        lambda pid: 15.0 if pid == 1234 else 16.0,
    )
    killpg = MagicMock()
    monkeypatch.setattr(os, "killpg", killpg)

    resource_sampling_module._terminate_process_tree(
        cast("subprocess.Popen[str]", process),
        leader_create_time=10.0,
        ownership_observed_until=20.0,
    )

    killpg.assert_not_called()


@pytest.mark.parametrize("exit_code", [0, 42])
def test_owned_command_preserves_captured_result_and_check_error(
    tmp_path: Path, exit_code: int
) -> None:
    command = [
        sys.executable,
        "-c",
        (
            "import sys; print('Unicode — output'); "
            f"print('diagnostic', file=sys.stderr); sys.exit({exit_code})"
        ),
    ]
    completed = resource_sampling_module.run_command_in_process_group(
        command,
        env=dict(os.environ),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert completed.args == command
    assert completed.returncode == exit_code
    assert completed.stdout == "Unicode — output\n"
    assert completed.stderr == "diagnostic\n"
    if exit_code:
        with pytest.raises(subprocess.CalledProcessError) as failed:
            resource_sampling_module.run_command_in_process_group(
                command,
                env=dict(os.environ),
                cwd=tmp_path,
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
        assert failed.value.cmd == command
        assert failed.value.returncode == exit_code
        assert failed.value.output == completed.stdout
        assert failed.value.stderr == completed.stderr


def test_owned_command_preserves_file_stdout_bytes_and_handle(tmp_path: Path) -> None:
    destination = tmp_path / "raw.log"
    with destination.open("w") as output:
        completed = resource_sampling_module.run_command_in_process_group(
            [sys.executable, "-c", "import os; os.write(1, bytes([255,0,10]))"],
            env=dict(os.environ),
            cwd=tmp_path,
            stdout=output,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        assert not output.closed
        assert completed.returncode == 0
        assert completed.stdout is None and completed.stderr is None
    assert destination.read_bytes() == bytes([255, 0, 10])


def test_owned_command_preserves_original_timeout_fields(tmp_path: Path) -> None:
    command = [sys.executable, "-c", "import time; print('partial', flush=True); time.sleep(60)"]
    with pytest.raises(subprocess.TimeoutExpired) as failed:
        resource_sampling_module.run_command_in_process_group(
            command,
            env=dict(os.environ),
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=1,
        )
    assert failed.value.cmd == command
    assert failed.value.timeout == 1
    assert failed.value.output == b"partial\n"


def test_owned_command_cleanup_refusal_preserves_timeout_without_unbounded_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A leader whose group cannot be verified is still killed, as subprocess.run would.

    The group is never signalled without proof of ownership, but the leader is this
    process's unreaped child, so its PID cannot have been recycled.
    """
    original = subprocess.TimeoutExpired(["fixture"], 1, output=b"retained", stderr=b"detail")
    timeouts: list[float | None] = []
    killed: list[str] = []

    class RefusedProcess:
        pid = 1234
        returncode = None
        stdin = None
        stdout = None
        stderr = None

        def poll(self) -> int | None:
            return None

        def kill(self) -> None:
            killed.append("leader")

        def communicate(self, *, timeout: float | None = None) -> tuple[str, str]:
            timeouts.append(timeout)
            if timeout is None:
                raise AssertionError("unbounded communicate")
            # A real wait takes its slice; sleeping keeps the leader poll from spinning.
            time.sleep(min(timeout, resource_sampling_module._COMMAND_POLL_INTERVAL_S))
            raise original

        def wait(self, timeout: float | None = None) -> int:
            assert timeout is not None, "unbounded wait"
            raise original

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: RefusedProcess())
    monkeypatch.setattr(resource_sampling_module, "_process_create_time", lambda _pid: None)
    with pytest.raises(subprocess.TimeoutExpired) as failed:
        resource_sampling_module.run_command_in_process_group(["fixture"], env={}, timeout=1)
    assert failed.value is original
    assert failed.value.timeout == 1
    assert failed.value.output == b"retained" and failed.value.stderr == b"detail"
    assert timeouts and all(timeout is not None for timeout in timeouts)
    assert killed == ["leader"]


def _reaped(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def _kill_leftover(pid_file: Path) -> None:
    if not pid_file.exists():
        return
    with suppress(psutil.NoSuchProcess, ValueError):
        leftover = psutil.Process(int(pid_file.read_text(encoding="utf-8")))
        if leftover.status() != psutil.STATUS_ZOMBIE:
            leftover.kill()


@pytest.mark.skipif(sys.platform == "win32", reason="owned process groups are POSIX")
def test_owned_command_timeout_reaps_a_descendant_of_a_plain_command(tmp_path: Path) -> None:
    """Cleanup belongs to the launcher, not to a shell wrapper in the command.

    A bare executable that forks a grandchild with detached stdio and then hangs is
    the shape of any agent CLI running a tool; the grandchild must not outlive the
    leader's timeout.
    """
    pid_file = tmp_path / "grandchild.pid"
    command = [
        sys.executable,
        "-c",
        (
            "import subprocess, sys, time; "
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
            "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
            f"open({str(pid_file)!r}, 'w').write(str(child.pid)); "
            "time.sleep(60)"
        ),
    ]
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            resource_sampling_module.run_command_in_process_group(
                command, env=dict(os.environ), cwd=tmp_path, capture_output=True, timeout=3
            )
        assert pid_file.exists(), "fixture command did not start"
        assert _reaped(int(pid_file.read_text(encoding="utf-8"))), (
            "the leader's descendant survived the timeout"
        )
    finally:
        _kill_leftover(pid_file)


@pytest.mark.skipif(sys.platform == "win32", reason="owned process groups are POSIX")
def test_owned_command_collects_a_leader_whose_descendant_holds_its_pipes(
    tmp_path: Path,
) -> None:
    """A tool subprocess that inherited the leader's stdout must not turn a finished
    command into a timeout: the leader is polled, the survivor terminated, the output
    collected."""
    pid_file = tmp_path / "grandchild.pid"
    command = [
        sys.executable,
        "-c",
        (
            "import subprocess, sys; "
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            f"open({str(pid_file)!r}, 'w').write(str(child.pid)); "
            "print('leader done', flush=True)"
        ),
    ]
    started = time.monotonic()
    try:
        completed = resource_sampling_module.run_command_in_process_group(
            command, env=dict(os.environ), cwd=tmp_path, capture_output=True, timeout=30
        )
        assert completed.returncode == 0
        assert completed.stdout == "leader done\n"
        assert time.monotonic() - started < 15, "the leader's exit was not noticed"
        assert pid_file.exists(), "fixture command did not start"
        assert _reaped(int(pid_file.read_text(encoding="utf-8"))), (
            "the pipe-holding descendant survived the command"
        )
    finally:
        _kill_leftover(pid_file)


@pytest.mark.skipif(sys.platform == "win32", reason="owned process groups are POSIX")
def test_cleanup_refusal_kills_only_the_unreaped_leader(monkeypatch: pytest.MonkeyPatch) -> None:
    process = MagicMock(pid=1234)
    process.poll.return_value = None
    process.wait.return_value = 0
    killpg = MagicMock()
    monkeypatch.setattr(os, "killpg", killpg)

    resource_sampling_module._terminate_process_tree(
        cast("subprocess.Popen[str]", process),
        leader_create_time=None,
        ownership_observed_until=20.0,
    )

    killpg.assert_not_called()
    process.kill.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=resource_sampling_module._PROCESS_KILL_WAIT_S)


_CALLER_SIGNALS: list[int] = [] if sys.platform == "win32" else [signal.SIGTERM, signal.SIGHUP]

# The caller under test: a Python process that runs one owned command and otherwise
# has the dispositions a terminal-launched metaproc has.
_SIGNALLED_CALLER = """
import os
import signal
import sys
from metaproc.engine.resource_sampling import run_command_in_process_group
signal.signal(signal.SIGTERM, signal.SIG_DFL)
signal.signal(signal.SIGHUP, signal.SIG_DFL)
run_command_in_process_group(
    [sys.executable, "-c", os.environ["FIXTURE_BODY"]],
    env=dict(os.environ),
    capture_output=True,
    timeout=60,
)
"""


@pytest.mark.parametrize("signum", _CALLER_SIGNALS)
def test_signal_to_the_caller_reaps_the_owned_group_before_the_caller_dies(
    tmp_path: Path, signum: int
) -> None:
    """The command's session no longer receives a signal sent to the caller's group,
    so the launcher must turn that signal into group cleanup before the caller dies
    by it, as it did when the command shared the caller's group."""
    pid_file = tmp_path / "pids"
    body = (
        "import os, subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        f"open({str(pid_file)!r}, 'w').write(f'{{os.getpid()}} {{child.pid}}'); "
        "time.sleep(60)"
    )
    caller = subprocess.Popen(
        [sys.executable, "-c", _SIGNALLED_CALLER],
        env={**os.environ, "FIXTURE_BODY": body},
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    pids: list[int] = []
    try:
        deadline = time.monotonic() + 20
        while len(pids) < 2 and time.monotonic() < deadline:
            if pid_file.exists():
                pids = [int(value) for value in pid_file.read_text(encoding="utf-8").split()]
            if len(pids) < 2:
                time.sleep(0.05)
        assert len(pids) == 2, "fixture command did not start"
        os.kill(caller.pid, signum)
        _, stderr = caller.communicate(timeout=30)
        assert caller.returncode == -signum, stderr
        leader, grandchild = pids
        assert _reaped(leader), "the command leader survived the caller's signal"
        assert _reaped(grandchild), "the command's descendant survived the caller's signal"
    finally:
        if caller.poll() is None:
            caller.kill()
            caller.wait(timeout=5)
        for pid in pids:
            with suppress(psutil.NoSuchProcess):
                leftover = psutil.Process(pid)
                if leftover.status() != psutil.STATUS_ZOMBIE:
                    leftover.kill()


@pytest.mark.skipif(sys.platform == "win32", reason="owned process groups are POSIX")
def test_caller_signal_watch_replaces_only_default_dispositions() -> None:
    """An ignored SIGHUP (nohup) stays ignored; a default SIGTERM is watched and then
    restored."""
    previous_term = signal.signal(signal.SIGTERM, signal.SIG_DFL)
    previous_hup = signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        with resource_sampling_module._caller_signals_end_the_command() as watch:
            assert signal.getsignal(signal.SIGTERM) == watch.handle
            assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
        assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGHUP, previous_hup)


@pytest.mark.skipif(sys.platform == "win32", reason="owned process groups are POSIX")
def test_caller_signal_watch_keeps_a_callers_own_handler() -> None:
    def policy(signum: int, frame: object) -> None:
        del signum, frame

    previous = signal.signal(signal.SIGTERM, policy)
    try:
        with resource_sampling_module._caller_signals_end_the_command():
            assert signal.getsignal(signal.SIGTERM) is policy
        assert signal.getsignal(signal.SIGTERM) is policy
    finally:
        signal.signal(signal.SIGTERM, previous)


@pytest.mark.skipif(sys.platform == "win32", reason="owned process groups are POSIX")
def test_caller_signal_watch_installs_nothing_off_the_main_thread() -> None:
    previous = signal.signal(signal.SIGTERM, signal.SIG_DFL)
    seen: dict[str, object] = {}

    def run() -> None:
        with resource_sampling_module._caller_signals_end_the_command() as watch:
            seen["sigterm"] = signal.getsignal(signal.SIGTERM)
            seen["watch"] = watch

    try:
        worker = threading.Thread(target=run)
        worker.start()
        worker.join(timeout=10)
        assert seen["sigterm"] == signal.SIG_DFL
        assert seen["watch"] is not None
    finally:
        signal.signal(signal.SIGTERM, previous)
