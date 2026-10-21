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
from contextlib import nullcontext, suppress
from pathlib import Path
from textwrap import dedent
from typing import override

import psutil
import pytest
from typer.testing import CliRunner

from metaproc.adapters.base import AuthStatus
from metaproc.adapters.gemini_cli import GeminiCliAdapter
from metaproc.cli import app
from metaproc.commands.auth_check import _run_live_check
from metaproc.commands.probe_tool_use import _run_probe
from metaproc.io import fmf_write, read_yaml_file
from metaproc.runpool.backend import (
    LaunchHandle,
    LocalBackend,
    PreparedLaunch,
    launch_and_supervise,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Gemini launcher uses POSIX sh")

# The deadline a direct consumer gives the fake CLI, which starts Python through sh. A
# one-second deadline lost that race under load, before the CLI could record its PID
# or send its interrupt; the headroom keeps the stop a stop and not a start failure.
_STOP_TIMEOUT_S = 5


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


def test_empty_prompt_delivers_eof_without_changing_audit_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, prompt = _prepare(
        tmp_path,
        monkeypatch,
        "import os, sys; from pathlib import Path; "
        "Path(os.environ['OUTPUT']).write_bytes(sys.stdin.buffer.read())",
    )
    prompt.write_bytes(b"")
    assert asyncio.run(launch_and_supervise(LocalBackend(), prepared, timeout_s=5)) == 0
    assert (tmp_path / "bytes").read_bytes() == b""
    assert prompt.read_bytes() == b""


@pytest.mark.parametrize("consumer", ["probe", "run-step", "auth-check"])
@pytest.mark.parametrize("stop", ["timeout", "interrupt"])
def test_direct_consumer_stop_does_not_leave_gemini_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, consumer: str, stop: str
) -> None:
    body = dedent(
        """
        import os
        import signal
        import time
        from pathlib import Path
        Path(os.environ['OUTPUT']).write_text(str(os.getpid()))
        os.close(1)
        os.close(2)
        if os.environ.get('INTERRUPT_PID'):
            time.sleep(0.1)
            os.kill(int(os.environ['INTERRUPT_PID']), signal.SIGINT)
        time.sleep(60)
        """
    )
    prepared, _ = _prepare(tmp_path, monkeypatch, body)
    assert prepared.env is not None
    monkeypatch.setenv("PATH", prepared.env["PATH"] + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("OUTPUT", prepared.env["OUTPUT"])
    monkeypatch.chdir(tmp_path)
    pid_file = tmp_path / "bytes"
    if stop == "interrupt":
        monkeypatch.setenv("INTERRUPT_PID", str(os.getpid()))
    try:
        with (
            pytest.raises(KeyboardInterrupt)
            if stop == "interrupt" and consumer != "run-step"
            else nullcontext()
        ):
            if consumer == "probe":
                ok, message, _ = _run_probe(
                    "gemini-cli", "gemini-3.6-flash", None, timeout_s=_STOP_TIMEOUT_S
                )
                assert not ok and "timed out" in message
            elif consumer == "auth-check":
                monkeypatch.setattr(
                    GeminiCliAdapter,
                    "check_auth",
                    lambda self: AuthStatus(
                        "gemini-cli",
                        True,
                        "fake-gemini",
                        True,
                        "fixture",
                        "no provider credentials",
                        "",
                    ),
                )
                results = _run_live_check(
                    "gemini-cli", "gemini-3.6-flash", timeout_s=_STOP_TIMEOUT_S
                )
                assert not results[0][0] and "timed out" in results[0][1]
            else:
                process = tmp_path / "timeout.process.md"
                fmf_write(
                    process,
                    "",
                    {
                        "process": {
                            "name": "timeout",
                            "defaults": {
                                "default_adapter": "gemini-cli",
                                "adapters": {
                                    "gemini-cli": {
                                        "type": "gemini-cli",
                                        "config": {
                                            "model": "gemini-3.6-flash",
                                            "timeout_s": _STOP_TIMEOUT_S,
                                            "working_directory": str(tmp_path),
                                            "native_settings_scope": "workspace",
                                        },
                                    }
                                },
                            },
                            "steps": [
                                {
                                    "id": "agent",
                                    "mode": "agent",
                                    "prompt_prefix": "wait",
                                    "outputs": {
                                        "main": {"path": "{{run.dir}}/out.md", "kind": "file"}
                                    },
                                }
                            ],
                        }
                    },
                )
                result = CliRunner().invoke(
                    app,
                    [
                        "run-step",
                        str(process),
                        "--step",
                        "agent",
                        "--wait",
                        "--var",
                        f"RUNS_DIR={tmp_path / 'runs'}",
                        "--var",
                        "RUN_ID=timeout",
                    ],
                )
                if stop == "interrupt":
                    assert result.exit_code == 130, result.output
                else:
                    assert result.exit_code != 0 and "timed out" in (
                        result.output + str(result.exception)
                    ), result.output
        assert pid_file.exists(), "fake Gemini did not start"
        with suppress(psutil.NoSuchProcess):
            process = psutil.Process(int(pid_file.read_text()))
            assert process.status() == psutil.STATUS_ZOMBIE, "Gemini survived its caller's stop"
    finally:
        if pid_file.exists():
            pid = int(pid_file.read_text())
            with suppress(psutil.NoSuchProcess):
                child = psutil.Process(pid)
                if child.status() != psutil.STATUS_ZOMBIE:
                    child.kill()
                    child.wait(timeout=5)


@pytest.mark.parametrize("mapped", [False, True], ids=["scalar", "for-each"])
def test_native_agent_paths_stream_prompt_once_per_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mapped: bool
) -> None:
    body = dedent(
        """
        import json
        import os
        import stat
        import sys
        from pathlib import Path
        if not stat.S_ISFIFO(os.fstat(0).st_mode):
            print('expected piped stdin', file=sys.stderr)
            sys.exit(42)
        content = sys.stdin.buffer.read()
        capture = Path(os.environ['CAPTURE_DIR'])
        capture.mkdir(exist_ok=True)
        item = os.environ['FAKE_ITEM']
        (capture / (item + '.prompt')).write_bytes(content)
        (capture / (item + '.argv')).write_text(json.dumps(sys.argv[1:]))
        output = Path(os.environ['OUTPUT_FILE'])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text('complete')
        print(json.dumps({'type': 'init', 'model': 'gemini-3.6-flash'}))
        print(json.dumps({'type': 'result', 'status': 'success', 'stats': {
            'models': {'gemini-3.6-flash': {'input_tokens': 7, 'output_tokens': 1}}}}))
        """
    )
    prepared, _ = _prepare(tmp_path, monkeypatch, body)
    assert prepared.env is not None
    monkeypatch.setenv("PATH", prepared.env["PATH"] + os.pathsep + os.environ["PATH"])
    process = tmp_path / "native.process.md"
    items = ["alfa", "brvo"] if mapped else ["scalar"]
    item = "{{ticker}}" if mapped else "scalar"
    marker = "complete Unicode — '$value'; prompt\n" * 4000
    step: dict[str, object] = {
        "id": "agent",
        "mode": "agent",
        "prompt_prefix": marker,
        "env": {
            "CAPTURE_DIR": str(tmp_path / "captures"),
            "FAKE_ITEM": item,
            "OUTPUT_FILE": "{{run.dir}}/" + item + ".txt",
        },
        "outputs": {"main": {"path": "{{run.dir}}/" + item + ".txt", "kind": "file"}},
    }
    spec: dict[str, object] = {
        "name": "native-transport",
        "defaults": {
            "retry": {"max_retries": 0},
            "default_adapter": "gemini-cli",
            "adapters": {
                "gemini-cli": {
                    "type": "gemini-cli",
                    "config": {
                        "model": "gemini-3.6-flash",
                        "permission_mode": "bypassPermissions",
                        "output_format": "stream-json",
                        "timeout_s": 5,
                        "working_directory": str(tmp_path),
                        "native_settings_scope": "workspace",
                    },
                }
            },
        },
        "steps": [step],
    }
    if mapped:
        roster = tmp_path / "items.md"
        fmf_write(roster, "", {"progress": {"items": [{"ticker": value} for value in items]}})
        spec["deps"] = {"items": {"path": str(roster), "as": "path"}}
        step["for_each"] = {
            "over": "deps.items",
            "bind": "ticker",
            "bind_fields": ["ticker"],
            "key": "{{ticker}}",
        }
    fmf_write(process, "", {"process": spec})
    result = CliRunner().invoke(
        app,
        [
            "run-process",
            str(process),
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
            "--var",
            "RUN_ID=native",
            "--max-concurrency",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output + str(result.exception)
    run = tmp_path / "runs/native"
    state = read_yaml_file(run / ".state/process-status.yaml")
    assert state["state"] == "completed"
    events = [
        json.loads(line) for line in (run / ".logs/runpool/events.jsonl").read_text().splitlines()
    ]
    assert sum(event["event"] == "pool_start" for event in events) == 1
    assert sum(event["event"] == "process_start" for event in events) == len(items)
    assert sum(event["event"] == "process_exit" for event in events) == len(items)
    for value in items:
        assert (run / (value + ".txt")).read_text() == "complete"
        captured = (tmp_path / "captures" / (value + ".prompt")).read_bytes()
        assert marker.encode() in captured
        invocations = list((run / ".logs/tasks/agent").rglob("*.invocation.json"))
        matching = []
        for invocation in invocations:
            record = json.loads(invocation.read_text())
            if Path(record["argv"][4]).read_bytes() == captured:
                matching.append(record)
        # Mapped prompts share body; each native invocation remains one task.
        assert len(invocations) == len(items)
        assert matching
        arguments = json.loads((tmp_path / "captures" / (value + ".argv")).read_text())
        assert arguments[arguments.index("-m") + 1] == "gemini-3.6-flash"


def test_successful_probe_reaps_a_descendant_after_the_cli_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = dedent(
        """
        import os
        import re
        import subprocess
        import sys
        from pathlib import Path
        prompt = sys.stdin.read()
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        Path(os.environ['OUTPUT']).write_text(str(child.pid))
        target = re.search(r'file (.*?)\\?', prompt).group(1)
        print(Path(target).read_text().splitlines()[0])
        """
    )
    prepared, _ = _prepare(tmp_path, monkeypatch, body)
    assert prepared.env is not None
    monkeypatch.setenv("PATH", prepared.env["PATH"] + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("OUTPUT", prepared.env["OUTPUT"])
    monkeypatch.chdir(tmp_path)
    pid_file = tmp_path / "bytes"
    try:
        ok, message, _ = _run_probe("gemini-cli", "gemini-3.6-flash", None, timeout_s=5)
        assert ok, message
        with suppress(psutil.NoSuchProcess):
            assert psutil.Process(int(pid_file.read_text())).status() == psutil.STATUS_ZOMBIE
    finally:
        if pid_file.exists():
            with suppress(psutil.NoSuchProcess):
                child = psutil.Process(int(pid_file.read_text()))
                if child.status() != psutil.STATUS_ZOMBIE:
                    child.kill()
                    child.wait(timeout=5)
