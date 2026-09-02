"""The launch-primitive spike: no fork of the supervisor, exit without SIGCHLD."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
from pathlib import Path

import pytest

from safeproc.launch import (
    ExitKind,
    Handshake,
    Launched,
    read_handshake,
    read_handshake_async,
    spawn_isolated,
    wait_exit,
    wait_exit_async,
)

pytestmark = pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="posix launch")

SESSION_PROBE = "import os; print(os.getpid(), os.getsid(0), os.getpgid(0), os.getcwd())"


def _finish(launched: Launched) -> None:
    launched.send_signal(signal.SIGKILL)
    with contextlib.suppress(TimeoutError):
        wait_exit(launched, 5.0)
    launched.close()


def test_direct_spawn_creates_its_own_session_and_group(tmp_path: Path) -> None:
    out = (tmp_path / "out.txt").open("wb")
    try:
        launched = spawn_isolated(
            [sys.executable, "-c", SESSION_PROBE], wrapper=False, stdout=out.fileno()
        )
        status = wait_exit(launched, 10.0)
    finally:
        out.close()
    assert status.ok
    pid, sid, pgid, _ = (tmp_path / "out.txt").read_text().split()
    assert pid == sid == pgid == str(launched.pid)
    assert sid != str(os.getsid(0)), "the child must not share the supervisor's session"
    launched.close()


def test_wrapper_handshake_reports_executing_and_honors_cwd(tmp_path: Path) -> None:
    out_path = tmp_path / "out.txt"
    out = out_path.open("wb")
    try:
        launched = spawn_isolated(
            [sys.executable, "-c", SESSION_PROBE], cwd=str(tmp_path), stdout=out.fileno()
        )
        assert read_handshake(launched, 10.0) is Handshake.EXECUTING
        status = wait_exit(launched, 10.0)
    finally:
        out.close()
    assert status.ok
    pid, sid, pgid, cwd = out_path.read_text().split()
    assert pid == sid == pgid == str(launched.pid), "exec keeps the wrapper's pid and session"
    assert os.path.realpath(cwd) == os.path.realpath(str(tmp_path))
    launched.close()


def test_wrapper_failure_is_distinguishable_from_target_exit() -> None:
    launched = spawn_isolated(["/bin/true"], cwd="/nonexistent/directory/for/safeproc")
    assert read_handshake(launched, 10.0) is Handshake.WRAPPER_FAILED
    status = wait_exit(launched, 10.0)
    assert status.kind is ExitKind.EXITED and status.code == 126
    launched.close()


def test_missing_target_reports_not_found_after_the_handshake() -> None:
    """The ready byte means registered and about to exec; an exec failure is exit 127."""
    launched = spawn_isolated(["/definitely/not/a/binary"])
    assert read_handshake(launched, 10.0) is Handshake.EXECUTING
    status = wait_exit(launched, 10.0)
    assert status.code == 127
    launched.close()


def test_immediate_exit_is_observed_not_lost() -> None:
    launched = spawn_isolated([sys.executable, "-c", "raise SystemExit(7)"], wrapper=False)
    status = wait_exit(launched, 10.0)
    assert status.kind is ExitKind.EXITED and status.code == 7
    launched.close()


def test_signal_goes_to_the_identity() -> None:
    launched = spawn_isolated([sys.executable, "-c", "import time; time.sleep(30)"], wrapper=False)
    assert launched.send_signal(signal.SIGTERM)
    status = wait_exit(launched, 10.0)
    assert status.kind is ExitKind.SIGNALED and status.code == signal.SIGTERM
    assert not launched.send_signal(signal.SIGTERM), "a reaped identity is gone"
    launched.close()


def test_wait_times_out_without_reaping() -> None:
    launched = spawn_isolated([sys.executable, "-c", "import time; time.sleep(30)"], wrapper=False)
    with pytest.raises(TimeoutError):
        wait_exit(launched, 0.2)
    _finish(launched)


@pytest.mark.skipif(sys.platform != "linux", reason="pidfd")
def test_linux_uses_a_pidfd() -> None:
    launched = spawn_isolated(["/bin/true"], wrapper=False)
    assert launched.pidfd is not None
    wait_exit(launched, 10.0)
    launched.close()


def test_async_exit_observation_uses_the_loop_not_sigchld() -> None:
    async def scenario() -> tuple[Handshake, int, int]:
        loop = asyncio.get_running_loop()
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.01)

        task = loop.create_task(ticker())
        launched = spawn_isolated([sys.executable, "-c", "import time; time.sleep(0.3)"])
        async with asyncio.timeout(10.0):
            handshake = await read_handshake_async(launched)
            status = await wait_exit_async(launched)
        task.cancel()
        launched.close()
        return handshake, status.code, ticks

    handshake, code, ticks = asyncio.run(scenario())
    assert handshake is Handshake.EXECUTING
    assert code == 0
    assert ticks > 5, "the loop kept running while the exit was awaited"


def test_many_concurrent_launches_share_one_loop() -> None:
    async def scenario() -> list[int]:
        launches = [
            spawn_isolated([sys.executable, "-c", f"raise SystemExit({i})"], wrapper=False)
            for i in range(12)
        ]
        async with asyncio.timeout(10.0):
            results = await asyncio.gather(*(wait_exit_async(item) for item in launches))
        for item in launches:
            item.close()
        return [status.code for status in results]

    assert asyncio.run(scenario()) == list(range(12))
