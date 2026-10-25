"""The gcp-run console wrapper keeps a job's console in a file, not in Cloud Logging."""

from __future__ import annotations

import io
import json
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from metaproc.cloud.gcp import gcp_run_console as console


class RecordingSink:
    def __init__(self) -> None:
        self.objects: dict[str, str] = {}
        self.puts: list[str] = []

    def put(self, name: str, text: str, content_type: str) -> None:
        del content_type
        self.objects[name] = text
        self.puts.append(name)


class FailingSink:
    def __init__(self) -> None:
        self.calls = 0

    def put(self, name: str, text: str, content_type: str) -> None:
        del name, text, content_type
        self.calls += 1
        raise RuntimeError("bucket unavailable")


def _python(code: str) -> list[str]:
    return [sys.executable, "-c", textwrap.dedent(code)]


def _settings(tmp_path: Path, **kwargs: object) -> console.ConsoleSettings:
    return console.ConsoleSettings(
        console_path=tmp_path / "logs" / "gcp-run" / "job-1" / "console.log",
        **kwargs,  # pyright: ignore[reportArgumentType]
    )


def test_child_output_goes_to_the_console_file_and_not_to_stdout(tmp_path: Path) -> None:
    settings = _settings(tmp_path, heartbeat_s=5.0)
    stdout = io.StringIO()
    # The words are built at run time so the console's header line, which records the
    # command, cannot contain them.
    code = console.run_with_console(
        _python("import sys; print('child', 'out'); print('child', 'err', file=sys.stderr)"),
        settings,
        sink=None,
        stdout=stdout,
    )
    assert code == 0
    text = settings.console_path.read_text()
    assert "child out" in text and "child err" in text
    assert "child out" not in stdout.getvalue() and "child err" not in stdout.getvalue()
    assert f"console {settings.console_path}" in stdout.getvalue()
    assert "exit 0" in stdout.getvalue()


def test_exit_code_and_signal_deaths_propagate(tmp_path: Path) -> None:
    assert (
        console.run_with_console(
            _python("raise SystemExit(7)"), _settings(tmp_path), sink=None, stdout=io.StringIO()
        )
        == 7
    )
    killed = console.run_with_console(
        _python("import os, signal; os.kill(os.getpid(), signal.SIGKILL)"),
        _settings(tmp_path),
        sink=None,
        stdout=io.StringIO(),
    )
    assert killed == 128 + signal.SIGKILL


def test_a_second_job_appends_rather_than_replacing_the_console(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    for word in ("first", "second"):
        console.run_with_console(
            _python(f"print('{word}')"), settings, sink=None, stdout=io.StringIO()
        )
    text = settings.console_path.read_text()
    assert text.index("first") < text.index("second")


def test_heartbeats_reach_stdout_and_refresh_the_live_view(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run-abc"
    settings = _settings(tmp_path, heartbeat_s=0.2, tail_lines=2, watch_dirs=(run_dir,))
    sink = RecordingSink()
    stdout = io.StringIO()
    code = console.run_with_console(
        _python(
            """
            import time
            for i in range(5):
                print(f"line {i}", flush=True)
                time.sleep(0.15)
            """
        ),
        settings,
        sink=sink,
        stdout=stdout,
        run_status=lambda path: f"status of {path.name}\n",
    )
    assert code == 0
    assert "running 0:00:00" in stdout.getvalue()
    assert "line 0" not in stdout.getvalue()
    assert sink.objects[console.LIVE_TAIL_NAME].splitlines() == ["line 3", "line 4"]
    assert sink.objects["runs/run-abc.txt"] == "status of run-abc\n"
    record = json.loads(sink.objects[console.LIVE_STATUS_NAME])
    assert record["state"] == "exited" and record["exit_code"] == 0
    assert record["console_lines"] == 6  # the header line and five child lines
    assert record["console_path"] == str(settings.console_path)
    # The status record is written after the tail on every publish.
    assert sink.puts[-1] == console.LIVE_STATUS_NAME
    assert sink.puts.count(console.LIVE_STATUS_NAME) >= 2


def test_live_view_failures_never_fail_the_job(tmp_path: Path) -> None:
    sink = FailingSink()
    stdout = io.StringIO()
    code = console.run_with_console(
        _python("print('ok')"), _settings(tmp_path), sink=sink, stdout=stdout
    )
    assert code == 0
    assert sink.calls >= 1
    assert "live view update failed (1): bucket unavailable" in stdout.getvalue()


def test_sigterm_reaches_the_command_and_its_children(tmp_path: Path) -> None:
    settings = _settings(tmp_path, heartbeat_s=0.2)
    script = tmp_path / "trap.sh"
    script.write_text("trap 'echo got TERM; exit 42' TERM\nsleep 30 &\nwait\n")
    wrapper = subprocess.Popen(
        [sys.executable, "-m", console.CONSOLE_MODULE, "--", "bash", str(script)],
        env={
            console.CONSOLE_PATH_ENV: str(settings.console_path),
            "PATH": "/usr/bin:/bin",
        },
        stdout=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while not settings.console_path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.5)
    wrapper.send_signal(signal.SIGTERM)
    out, _ = wrapper.communicate(timeout=10)
    assert wrapper.returncode == 42
    assert "got TERM" in settings.console_path.read_text()
    assert "exit 42" in out


def test_read_tail_returns_the_last_lines_of_a_large_file(tmp_path: Path) -> None:
    path = tmp_path / "console.log"
    path.write_text("".join(f"line {i}\n" for i in range(50_000)))
    assert console.read_tail(path, 3).splitlines() == ["line 49997", "line 49998", "line 49999"]
    assert console.read_tail(path, 0) == ""


def test_counter_reads_only_new_bytes(tmp_path: Path) -> None:
    path = tmp_path / "console.log"
    path.write_text("a\nb\n")
    counter = console.ConsoleCounter(path)
    counter.refresh()
    assert (counter.bytes, counter.lines) == (4, 2)
    with path.open("a") as handle:
        handle.write("c\npartial")
    counter.refresh()
    assert (counter.bytes, counter.lines) == (13, 3)


def test_settings_require_a_console_path() -> None:
    with pytest.raises(ValueError, match=console.CONSOLE_PATH_ENV):
        console.ConsoleSettings.from_env({})
    settings = console.ConsoleSettings.from_env(
        {
            console.CONSOLE_PATH_ENV: "/mnt/filestore/logs/gcp-run/j/console.log",
            console.LIVE_GCS_ENV: "gs://bucket/gcp-run/j/live",
            console.WATCH_ENV: "/mnt/filestore/runs/run-a,,/mnt/filestore/runs/run-b",
        }
    )
    assert settings.watch_dirs == (
        Path("/mnt/filestore/runs/run-a"),
        Path("/mnt/filestore/runs/run-b"),
    )
    assert settings.live_gcs == "gs://bucket/gcp-run/j/live"


def test_main_refuses_without_a_command_or_console(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(console.CONSOLE_PATH_ENV, raising=False)
    assert console.main(["--"]) == 2
    assert console.main(["--", "true"]) == 2


def test_watched_run_status_refreshes_on_its_own_slower_cadence(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run-abc"
    settings = _settings(tmp_path, watch_dirs=(run_dir,), run_status_s=300.0)
    settings.console_path.parent.mkdir(parents=True)
    settings.console_path.write_text("x\n")
    calls: list[str] = []
    times = iter([0.0, 60.0, 120.0, 310.0, 320.0])
    live = console.LiveView(
        settings,
        RecordingSink(),
        ["true"],
        io.StringIO(),
        run_status=lambda path: calls.append(path.name) or "ok\n",
        clock=lambda: next(times),
    )
    counter = console.ConsoleCounter(settings.console_path)
    for _ in range(4):
        live.publish(counter)
    assert calls == ["run-abc", "run-abc"]  # at 0s and at 310s, not at 60s or 120s
    live.publish(counter, final=True)
    assert calls == ["run-abc", "run-abc", "run-abc"]
