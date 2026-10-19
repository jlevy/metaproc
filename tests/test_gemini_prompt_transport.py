"""Provider-free prompt transport and native subprocess lifecycle regressions."""

from __future__ import annotations

import asyncio
import errno
import json
import os
import shlex
import shutil
import stat
import sys
from pathlib import Path
from textwrap import dedent
from typing import override

import psutil
import pytest

from metaproc.adapters.gemini_cli import GeminiCliAdapter
from metaproc.runpool.backend import (
    LaunchHandle,
    LocalBackend,
    PreparedLaunch,
    launch_and_supervise,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Gemini launcher uses POSIX sh")


def _prepare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
    *,
    executable: str = sys.executable,
) -> tuple[PreparedLaunch, Path]:
    monkeypatch.setenv("METAPROC_SKIP_GEMINI_VERSION_CHECK", "1")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = tmp_path / "fake-cli"
    script.write_text(body)
    fake = bin_dir / "gemini"
    fake.write_text(f'#!/bin/sh\nexec {shlex.quote(executable)} {shlex.quote(str(script))} "$@"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    prompt = tmp_path / "audit 'prompt $value;.md"
    prompt.write_bytes(("full prompt — '$value'\n" * 10000).encode())
    command = GeminiCliAdapter().build_command(prompt, {"model": "gemini-3.6-flash"}, {})
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.defpath}", "OUTPUT": str(tmp_path / "bytes")}
    return PreparedLaunch(
        command=tuple(command), env=env, cwd=tmp_path, log_path=tmp_path / "cli.jsonl"
    ), prompt


def test_delayed_file_read_still_delivers_the_complete_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to reproduce the Gemini stdin-reader boundary")
    # One blocked FIFO open occupies the only libuv worker without CPU work. A
    # regular-file stdin read then queues behind it; pipe reads remain available.
    # This is the CLI's 500ms no-input boundary, without loading its auth or main.
    body = dedent(
        """
        const fs = require('node:fs');
        fs.open(process.env.BLOCKER, 'r', () => {});
        fs.writeFileSync(process.env.BLOCKER_READY, 'ready');
        let input = '';
        const timer = setTimeout(() => {
          console.error('No input provided via stdin.');
          process.exit(42);
        }, 500);
        process.stdin.setEncoding('utf8');
        process.stdin.on('data', chunk => { input += chunk; });
        process.stdin.on('end', () => {
          clearTimeout(timer);
          fs.writeFileSync(process.env.OUTPUT, input);
          process.exit(0);
        });
        """
    )
    prepared, prompt = _prepare(tmp_path, monkeypatch, body, executable=node)
    blocker = tmp_path / "blocked-worker"
    os.mkfifo(blocker)
    assert prepared.env is not None
    prepared.env.update(
        {
            "BLOCKER": str(blocker),
            "BLOCKER_READY": str(tmp_path / "worker-ready"),
            "UV_THREADPOOL_SIZE": "1",
        }
    )

    async def run() -> int:
        task = asyncio.create_task(launch_and_supervise(LocalBackend(), prepared, timeout_s=10))
        for _ in range(500):
            if (tmp_path / "worker-ready").exists():
                break
            await asyncio.sleep(0.01)
        assert (tmp_path / "worker-ready").exists(), "fake CLI worker did not start"
        # Release the worker after the input deadline so Node can drain its worker
        # during process.exit, without leaving an uninterruptible fixture behind.
        await asyncio.sleep(1)
        try:
            fd = os.open(blocker, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno != errno.ENXIO:
                raise
        else:
            os.close(fd)
        return await task

    code = asyncio.run(run())
    assert code == 0, prepared.log_path.read_text() if prepared.log_path else ""
    assert (tmp_path / "bytes").read_bytes() == prompt.read_bytes()


@pytest.mark.parametrize("exit_code", [0, 42])
def test_prompt_bytes_exit_status_and_invocation_are_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int
) -> None:
    body = dedent(
        f"""
        import os
        import sys
        from pathlib import Path
        Path(os.environ['OUTPUT']).write_bytes(sys.stdin.buffer.read())
        sys.exit({exit_code})
        """
    )
    prepared, prompt = _prepare(tmp_path, monkeypatch, body)
    original = prompt.read_bytes()
    code = asyncio.run(launch_and_supervise(LocalBackend(), prepared, timeout_s=5))
    assert code == exit_code
    assert (tmp_path / "bytes").read_bytes() == original
    assert prompt.read_bytes() == original
    invocation = json.loads((tmp_path / "cli.jsonl.invocation.json").read_text())
    assert invocation["argv"] == list(prepared.command)
    assert invocation["cwd"] == str(tmp_path)
    assert str(prompt) in invocation["argv"]
    assert original.decode() not in invocation["argv"]


def test_early_cli_failure_preserves_exit_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, prompt = _prepare(tmp_path, monkeypatch, "import sys; sys.exit(42)")
    prompt.write_bytes(b"x" * 1048576)
    code = asyncio.run(launch_and_supervise(LocalBackend(), prepared, timeout_s=5))
    assert code == 42


def test_missing_prompt_does_not_start_the_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, prompt = _prepare(
        tmp_path,
        monkeypatch,
        "import os; from pathlib import Path; Path(os.environ['OUTPUT']).touch()",
    )
    prompt.unlink()
    code = asyncio.run(launch_and_supervise(LocalBackend(), prepared, timeout_s=5))
    assert code != 0
    assert not (tmp_path / "bytes").exists()


@pytest.mark.parametrize("stop", ["cancel", "timeout"])
def test_native_supervision_reaps_the_entire_prompt_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stop: str
) -> None:
    body = dedent(
        """
        import os
        import subprocess
        import sys
        import time
        from pathlib import Path
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        Path(os.environ['OUTPUT']).write_text(f'{os.getpid()} {child.pid}')
        time.sleep(60)
        """
    )
    prepared, prompt = _prepare(tmp_path, monkeypatch, body)
    # The consumer deliberately never reads stdin; the large prompt keeps the
    # producer blocked in the pipe, exercising cleanup of every pipeline member.
    prompt.write_bytes(b"x" * 1048576)

    class RecordingBackend(LocalBackend):
        handle: LaunchHandle | None = None

        @override
        async def launch(self, prepared: PreparedLaunch, label: str = "") -> LaunchHandle:
            self.handle = await super().launch(prepared, label)
            return self.handle

    async def run() -> None:
        backend = RecordingBackend()
        task = asyncio.create_task(
            launch_and_supervise(backend, prepared, timeout_s=1 if stop == "timeout" else 5)
        )
        ready = tmp_path / "bytes"
        try:
            for _ in range(200):
                if ready.exists():
                    break
                await asyncio.sleep(0.01)
            assert ready.exists(), "fake CLI did not start"
            assert backend.handle is not None and backend.handle.pid is not None
            leader = psutil.Process(backend.handle.pid)
            pids = {leader.pid, *(process.pid for process in leader.children(recursive=True))}
            pids.update(int(value) for value in ready.read_text().split())
            health = await backend.health(backend.handle)
            assert health is not None and health.descendants is not None
            assert health.descendants >= 1
            assert health.rss_bytes is not None
            assert (
                health.rss_bytes
                >= psutil.Process(int(ready.read_text().split()[0])).memory_info().rss
            )
            if stop == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                with pytest.raises(TimeoutError):
                    await task
            for pid in pids:
                assert (
                    not psutil.pid_exists(pid)
                    or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
                )
        finally:
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

    asyncio.run(run())
