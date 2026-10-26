"""Offline regressions for console publication, supervision and log routing boundaries."""

from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.cloud.gcp import gcp_run_console as console
from metaproc.cloud.gcp import gcp_run_live
from metaproc.commands import gcp
from metaproc.errors import CLIError


class Sink:
    def __init__(self) -> None:
        self.objects: dict[str, str] = {}
        self.on_put: Callable[[str], None] = lambda _name: None

    def put(self, name: str, text: str, content_type: str) -> None:
        self.objects[name] = text
        self.on_put(name)


def _publisher(tmp_path: Path, **kwargs: Any) -> tuple[Path, Any, Sink, Any]:
    path = tmp_path / "console.log"
    path.write_text("1\n2\n3\n")
    counter = console.ConsoleCounter(path)
    counter.refresh()
    sink = Sink()
    live = console.LiveView(
        console.ConsoleSettings(console_path=path, tail_lines=2, **kwargs),
        sink,
        ["offline"],
        io.StringIO(),
        run_status=lambda p: str(p),
    )
    return path, counter, sink, live


def _tail(sink: Sink, status: dict[str, Any]) -> str:
    return str(status.get("console_tail", sink.objects.get(console.LIVE_TAIL_NAME, "")))


def _job(state: str = "FAILED", name: str = "probe", live: bool = True) -> Any:
    variables = {console.LIVE_GCS_ENV: "gs://offline/live"} if live else {}
    runnable = SimpleNamespace(environment=SimpleNamespace(variables=variables))
    return SimpleNamespace(
        name=f"projects/offline/locations/us-central1/jobs/{name}",
        uid=f"uid-{name}",
        task_groups=[SimpleNamespace(task_spec=SimpleNamespace(runnables=[runnable]))],
        status=SimpleNamespace(state=state),
        labels={},
    )


def test_append_between_count_and_tail_cannot_shift_the_snapshot(tmp_path: Path) -> None:
    path, counter, sink, live = _publisher(tmp_path)
    with path.open("a") as handle:
        handle.write("4\n5\n")
    live.publish(counter)
    status = json.loads(sink.objects[console.LIVE_STATUS_NAME])
    assert status["console_lines"] == 3
    assert _tail(sink, status).splitlines() == ["2", "3"]


def test_reader_cannot_mix_status_and_legacy_tail_generations(tmp_path: Path) -> None:
    path, counter, sink, live = _publisher(tmp_path)
    live.publish(counter)
    with path.open("a") as handle:
        handle.write("4\n5\n")
    counter.refresh()
    observed: list[list[str]] = []

    class View:
        def status(self) -> dict[str, Any]:
            return json.loads(sink.objects[console.LIVE_STATUS_NAME])

        def tail(self) -> str:
            return sink.objects[console.LIVE_TAIL_NAME]

    def read_between_objects(name: str) -> None:
        if name == console.LIVE_TAIL_NAME:
            out = MagicMock()
            gcp._print_live_logs(
                [_job()], out=out, limit=2, follow=False, view_factory=lambda _: View()
            )
            observed.append([call.args[0] for call in out.data.call_args_list])

    sink.on_put = read_between_objects
    live.publish(counter)
    assert observed == [["2", "3"]]


def test_partial_line_is_hidden_until_complete_and_final_partial_is_counted(tmp_path: Path) -> None:
    path, counter, sink, live = _publisher(tmp_path)
    with path.open("a") as handle:
        handle.write("part")
    counter.refresh()
    live.publish(counter)
    status = json.loads(sink.objects[console.LIVE_STATUS_NAME])
    assert _tail(sink, status).splitlines() == ["2", "3"]
    with path.open("a") as handle:
        handle.write("ial\nlast")
    counter.refresh()
    live.publish(counter)
    status = json.loads(sink.objects[console.LIVE_STATUS_NAME])
    assert gcp_run_live.new_tail_lines(_tail(sink, status), 3, status["console_lines"]) == (
        ["partial"],
        0,
    )
    live.publish(counter, final=True)
    status = json.loads(sink.objects[console.LIVE_STATUS_NAME])
    assert gcp_run_live.new_tail_lines(_tail(sink, status), 4, status["console_lines"]) == (
        ["last"],
        0,
    )
    assert path.read_bytes().endswith(b"partial\nlast")


def test_wrapper_delivers_final_partial_without_changing_console_bytes(tmp_path: Path) -> None:
    path = tmp_path / "console.log"
    sink = Sink()
    assert (
        console.run_with_console(
            [sys.executable, "-c", "import sys; sys.stdout.write('final partial')"],
            console.ConsoleSettings(console_path=path),
            sink=sink,
            stdout=io.StringIO(),
        )
        == 0
    )
    status = json.loads(sink.objects[console.LIVE_STATUS_NAME])
    assert gcp_run_live.new_tail_lines(_tail(sink, status), 1, status["console_lines"]) == (
        ["final partial"],
        0,
    )
    assert path.read_bytes().endswith(b"\nfinal partial")


@pytest.mark.parametrize("state", ["FAILED", "CANCELLED", "SUCCEEDED"])
@pytest.mark.parametrize("record", [None, {"state": "running", "console_lines": 3}])
def test_terminal_batch_job_stops_follow_without_final_upload(state: str, record: Any) -> None:
    class View:
        def status(self) -> Any:
            return record

        def tail(self) -> str:
            return "1\n2\n3\n"

    polls = 0

    def bounded_sleep(_delay: float) -> None:
        nonlocal polls
        polls += 1
        if polls == 3:
            raise KeyboardInterrupt

    out = MagicMock()
    gcp._print_live_logs(
        [_job(state)],
        out=out,
        limit=20,
        follow=True,
        view_factory=lambda _: View(),
        sleep=bounded_sleep,
    )
    messages = [call.args[0] for call in out.progress.call_args_list]
    assert "Stopped following logs." not in messages
    assert any(state in message for message in messages)


def test_signal_during_spawn_reaches_child_and_previous_handler_is_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prior_calls: list[int] = []
    handler = lambda signum, _frame: prior_calls.append(signum)
    prior = signal.signal(signal.SIGTERM, handler)
    real_popen = subprocess.Popen

    def injected_popen(*args: Any, **kwargs: Any) -> Any:
        child = real_popen(*args, **kwargs)
        signal.raise_signal(signal.SIGTERM)
        return child

    monkeypatch.setattr(subprocess, "Popen", injected_popen)
    try:
        code = console.run_with_console(
            [sys.executable, "-c", "import time; time.sleep(0.2)"],
            console.ConsoleSettings(console_path=tmp_path / "console.log"),
            sink=None,
            stdout=io.StringIO(),
        )
        assert signal.getsignal(signal.SIGTERM) is handler
    finally:
        signal.signal(signal.SIGTERM, prior)
    assert code == 128 + signal.SIGTERM
    assert prior_calls == []


def test_spawn_failure_restores_signal_handlers(tmp_path: Path) -> None:
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    with pytest.raises(FileNotFoundError):
        console.run_with_console(
            [str(tmp_path / "nonexistent-command")],
            console.ConsoleSettings(console_path=tmp_path / "console.log"),
            sink=None,
            stdout=io.StringIO(),
        )
    assert {sig: signal.getsignal(sig) for sig in previous} == previous


def test_tail_scan_and_output_are_byte_bounded_and_utf8_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "long.log"
    path.write_bytes(b"x" + "🐈".encode() * (512 * 1024) + b"\n")
    original_open = Path.open
    reads: list[int] = []

    class Reader:
        def __init__(self, handle: Any) -> None:
            self.handle = handle

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *args: object) -> Any:
            return self.handle.__exit__(*args)

        def seek(self, *args: Any) -> Any:
            return self.handle.seek(*args)

        def tell(self) -> int:
            return self.handle.tell()

        def read(self, count: int = -1) -> bytes:
            data = self.handle.read(count)
            reads.append(len(data))
            return data

    def tracked_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        handle = original_open(self, *args, **kwargs)
        return Reader(handle) if self == path else handle

    monkeypatch.setattr(Path, "open", tracked_open)
    text = console.read_tail(path, 400)
    assert sum(reads) <= 256 * 1024
    assert len(text.encode()) <= 256 * 1024
    assert "�" not in text
    assert text.endswith("🐈\n")


def test_distinct_watch_paths_cannot_overwrite_one_basename(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="basename"):
        console.ConsoleSettings(
            console_path=tmp_path / "console.log",
            watch_dirs=(tmp_path / "first/run", tmp_path / "second/run"),
        )


@pytest.mark.parametrize("target_kind", ["resource", "run-key", "mixed"])
@pytest.mark.parametrize("flag", ["--include-agent-logs", "--errors"])
def test_requested_cloud_logs_are_not_hidden_by_file_route(
    monkeypatch: pytest.MonkeyPatch, target_kind: str, flag: str
) -> None:
    from google.cloud import batch_v1  # noqa: PLC0415 -- optional [gcp-batch] dependency
    from google.cloud import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
        logging as cloud_logging,
    )

    filters: list[str] = []
    jobs = [_job()]
    if target_kind == "mixed":
        jobs.append(_job(name="other", live=False))
    monkeypatch.setattr(gcp, "_require_gcp_batch", lambda: None)
    monkeypatch.setattr(
        batch_v1, "BatchServiceClient", lambda: SimpleNamespace(get_job=lambda _: jobs[0])
    )
    monkeypatch.setattr(gcp, "_query_jobs_by_run_id", lambda *args: jobs)
    monkeypatch.setattr(gcp, "_print_live_logs", lambda *args, **kwargs: pytest.fail("file route"))

    def entries(**kwargs: Any) -> list[Any]:
        filters.append(kwargs["filter_"])
        return []

    monkeypatch.setattr(
        cloud_logging, "Client", lambda **kwargs: SimpleNamespace(list_entries=entries)
    )
    target = jobs[0].name if target_kind == "resource" else "run-probe"
    result = CliRunner().invoke(app, ["gcp", "logs", target, "--project", "offline", flag])
    assert result.exit_code == 0, result.output
    assert len(filters) == 1
    assert ("batch_agent_logs" in filters[0]) == (flag == "--include-agent-logs")


@pytest.mark.parametrize("blocked", ["render", "upload"])
def test_blocked_monitoring_does_not_block_heartbeats_or_child_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blocked: str
) -> None:
    release = threading.Event()
    entered = threading.Event()
    output = io.StringIO()
    sink = Sink()
    monkeypatch.setattr(console, "FINALIZE_GRACE_SECONDS", 0.1, raising=False)

    def block() -> None:
        entered.set()
        release.wait(2)

    def render(_path: Path) -> str:
        block()
        return "cached status\n"

    class SlowSink(Sink):
        def put(self, name: str, text: str, content_type: str) -> None:
            block()
            super().put(name, text, content_type)

    settings = console.ConsoleSettings(
        console_path=tmp_path / "console.log",
        heartbeat_s=0.02,
        watch_dirs=(tmp_path / "run-a",) if blocked == "render" else (),
    )
    started = time.monotonic()
    try:
        code = console.run_with_console(
            [sys.executable, "-c", "import time; time.sleep(0.2)"],
            settings,
            sink=SlowSink() if blocked == "upload" else sink,
            stdout=output,
            run_status=render,
        )
        elapsed = time.monotonic() - started
        assert entered.is_set()
        assert code == 0
        assert elapsed < 1.5
        assert output.getvalue().count("running ") >= 3
        workers = [t for t in threading.enumerate() if t.name.startswith("gcp-console-")]
        assert len(workers) <= 2
        assert all(t.daemon for t in workers)
        if blocked == "render":
            assert json.loads(sink.objects[console.LIVE_STATUS_NAME])["state"] == "exited"
    finally:
        release.set()


def test_follow_refreshes_batch_after_a_blip_and_drains_the_last_available_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    states = iter([None, "FAILED"])
    records = [
        {"state": "running", "console_lines": 1, "snapshot_version": 1, "console_tail": "one\n"},
        {
            "state": "running",
            "console_lines": 2,
            "snapshot_version": 1,
            "console_tail": "one\ntwo\n",
        },
        {
            "state": "running",
            "console_lines": 3,
            "snapshot_version": 1,
            "console_tail": "two\nthree\n",
        },
    ]

    class View:
        index = 0

        def status(self) -> dict[str, Any]:
            calls.append("view")
            record = records[min(self.index, len(records) - 1)]
            self.index += 1
            return record

        def tail(self) -> str:
            return str(records[min(self.index - 1, len(records) - 1)]["console_tail"])

    class StateReader:
        def __call__(self, _name: str) -> str | None:
            calls.append("batch")
            return next(states)

        def close(self) -> None:
            calls.append("close")

    monkeypatch.setattr(gcp, "_BatchJobStateReader", StateReader, raising=False)
    polls = 0

    def sleep(_seconds: float) -> None:
        nonlocal polls
        polls += 1
        assert polls <= 2, "still following after Batch became terminal"

    out = MagicMock()
    gcp._print_live_logs(
        [_job("QUEUED")], out=out, limit=10, follow=True, view_factory=lambda _: View(), sleep=sleep
    )
    assert [call.args[0] for call in out.data.call_args_list] == ["one", "two", "three"]
    assert calls == ["view", "batch", "view", "batch", "view", "close"]
    assert "Batch FAILED" in out.progress.call_args_list[-2].args[0]


def test_repeated_batch_read_failures_stop_with_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class StateReader:
        def __call__(self, _name: str) -> None:
            return None

        def close(self) -> None:
            pass

    class View:
        def status(self) -> dict[str, Any]:
            return {"state": "running", "console_lines": 0}

        def tail(self) -> str:
            return ""

    monkeypatch.setattr(gcp, "_BatchJobStateReader", StateReader, raising=False)
    polls = 0

    def sleep(_seconds: float) -> None:
        nonlocal polls
        polls += 1
        assert polls <= 10, "kept following after ten failed Batch reads"

    with pytest.raises(CLIError, match="after 10 polls"):
        gcp._print_live_logs(
            [_job("SCHEDULED")],
            out=MagicMock(),
            limit=10,
            follow=True,
            view_factory=lambda _: View(),
            sleep=sleep,
        )


def test_giant_final_partial_is_one_marked_utf8_suffix(tmp_path: Path) -> None:
    path, counter, sink, live = _publisher(tmp_path)
    with path.open("a") as handle:
        handle.write("🐈" * 300_000)
    counter.refresh()
    live.publish(counter)
    running = json.loads(sink.objects[console.LIVE_STATUS_NAME])
    assert _tail(sink, running).splitlines() == ["2", "3"]
    live.publish(counter, final=True)
    final = json.loads(sink.objects[console.LIVE_STATUS_NAME])
    fresh, missed = gcp_run_live.new_tail_lines(_tail(sink, final), 3, final["console_lines"])
    assert missed == 0 and len(fresh) == 1
    assert len(fresh[0].encode()) <= 256 * 1024
    assert set(fresh[0]) == {"🐈"}
    assert final["tail_truncated"] and final["tail_start_partial"]
    assert path.read_bytes().endswith("🐈".encode())


def test_reader_bounds_legacy_download_before_decoding() -> None:
    downloaded: list[int] = []
    giant = b"x" * (2 * 1024 * 1024)

    class Blob:
        def __init__(self, name: str) -> None:
            self.name = name

        def exists(self, **kwargs: Any) -> bool:
            return self.name.endswith(console.LIVE_TAIL_NAME)

        def download_as_text(self) -> str:
            downloaded.append(len(giant))
            return giant.decode()

        def download_as_bytes(self, *, start: int, end: int, **kwargs: Any) -> bytes:
            data = giant[start : end + 1]
            downloaded.append(len(data))
            assert kwargs["retry"] is None
            return data

    client = SimpleNamespace(bucket=lambda _: SimpleNamespace(blob=Blob))
    view = gcp_run_live.LiveView("gs://offline/live", client_factory=lambda: client)
    with pytest.raises(ValueError, match="read limit"):
        view.tail()
    assert downloaded == [256 * 1024 + 1]


def test_uploads_have_no_implicit_retries_and_denial_preserves_workload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from google.api_core.exceptions import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
        Forbidden,
    )
    from google.cloud import storage  # noqa: PLC0415 -- optional [gcp-batch] dependency

    uploads: list[dict[str, Any]] = []

    def upload(_text: str, **kwargs: Any) -> None:
        uploads.append(kwargs)
        raise Forbidden("fixture: storage.objects.create denied")

    monkeypatch.setattr(
        storage,
        "Client",
        lambda: SimpleNamespace(
            bucket=lambda _: SimpleNamespace(
                blob=lambda _: SimpleNamespace(upload_from_string=upload)
            )
        ),
    )
    path = tmp_path / "console.log"
    out = io.StringIO()
    assert (
        console.run_with_console(
            [sys.executable, "-c", "print('completed')"],
            console.ConsoleSettings(console_path=path),
            sink=console.GcsLiveSink("gs://offline/live"),
            stdout=out,
        )
        == 0
    )
    assert path.read_text().endswith("completed\n")
    assert uploads[0]["timeout"] <= 5 and uploads[0]["retry"] is None
    assert "live view update failed" in out.getvalue()


def test_publication_budget_stops_starting_more_object_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, counter, sink, live = _publisher(tmp_path)
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    sink.on_put = lambda _: clock.__setitem__(0, 31.0)
    live.publish(counter)
    assert set(sink.objects) == {console.LIVE_TAIL_NAME}
    assert path.read_text() == "1\n2\n3\n"


def test_watch_normalization_deduplicates_without_changing_single_run_keys(tmp_path: Path) -> None:
    path = tmp_path / "run-a"
    settings = console.ConsoleSettings(
        console_path=tmp_path / "console.log", watch_dirs=(path, path / ".." / "run-a")
    )
    assert settings.watch_dirs == (path,)


def test_carriage_returns_do_not_advance_the_line_cursor() -> None:
    assert gcp_run_live.new_tail_lines("progress\rcomplete\nlast", 0, 2) == (
        ["progress\rcomplete", "last"],
        0,
    )


@pytest.mark.parametrize("prior_handler", ["SIG_DFL", "SIG_IGN"])
def test_startup_forwarding_with_default_and_ignored_parent_handlers(
    tmp_path: Path,
    prior_handler: str,
) -> None:
    pid_path = tmp_path / "child.pid"
    code = textwrap.dedent(f"""
        import io, os, signal, subprocess, sys
        from pathlib import Path
        from metaproc.cloud.gcp import gcp_run_console as console
        popen = subprocess.Popen
        def start(*args, **kwargs):
            child = popen(*args, **kwargs)
            Path({str(pid_path)!r}).write_text(str(child.pid))
            os.kill(os.getpid(), signal.SIGTERM)
            return child
        subprocess.Popen = start
        previous = signal.{prior_handler}
        signal.signal(signal.SIGTERM, previous)
        result = console.run_with_console(
            [sys.executable, '-c', 'import time; time.sleep(5)'],
            console.ConsoleSettings(console_path=Path({str(tmp_path / "console.log")!r})),
            sink=None, stdout=io.StringIO(),
        )
        print(result, signal.getsignal(signal.SIGTERM) == previous)
    """)
    try:
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=10, check=False
        )
    finally:
        if pid_path.exists():
            try:
                os.killpg(int(pid_path.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["143", "True"]


def test_monitor_thread_start_failure_does_not_kill_the_workload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = threading.Thread.start

    def limited_start(thread: threading.Thread) -> None:
        if thread.name == "gcp-console-publish":
            raise RuntimeError("fixture: no monitoring thread slot")
        start(thread)

    monkeypatch.setattr(threading.Thread, "start", limited_start)
    path = tmp_path / "console.log"
    out = io.StringIO()
    assert (
        console.run_with_console(
            [sys.executable, "-c", "print('workload completed')"],
            console.ConsoleSettings(console_path=path),
            sink=Sink(),
            stdout=out,
        )
        == 0
    )
    assert path.read_text().endswith("workload completed\n")
    assert "no monitoring thread slot" in out.getvalue()


def test_live_exit_is_drained_even_on_the_tenth_unavailable_batch_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StateReader:
        def __call__(self, _name: str) -> None:
            return None

        def close(self) -> None:
            pass

    class View:
        reads = 0

        def status(self) -> dict[str, Any]:
            self.reads += 1
            return {
                "snapshot_version": 1,
                "state": "exited" if self.reads == 11 else "running",
                "console_lines": 2 if self.reads == 11 else 1,
                "console_tail": "one\nlast\n" if self.reads == 11 else "one\n",
            }

    monkeypatch.setattr(gcp, "_BatchJobStateReader", StateReader, raising=False)
    out = MagicMock()
    gcp._print_live_logs(
        [_job("RUNNING")],
        out=out,
        limit=10,
        follow=True,
        view_factory=lambda _: View(),
        sleep=lambda _: None,
    )
    assert [call.args[0] for call in out.data.call_args_list] == ["one", "last"]
    assert out.progress.call_args_list[-1].args[0] == "Every followed job has exited."


def _storage_unavailable() -> Exception:
    from google.api_core.exceptions import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
        ServiceUnavailable,
    )

    return ServiceUnavailable("fixture: storage unavailable")


class _RunningBatchState:
    def __call__(self, _name: str) -> str:
        return "RUNNING"

    def close(self) -> None:
        pass


def _bounded_sleep(limit: int) -> Callable[[float], None]:
    polls = 0

    def sleep(_seconds: float) -> None:
        nonlocal polls
        polls += 1
        assert polls <= limit, f"still following after {limit} polls"

    return sleep


@pytest.mark.parametrize("failed_polls", [1, 9])
def test_transient_live_view_read_errors_do_not_end_a_follow(
    monkeypatch: pytest.MonkeyPatch, failed_polls: int
) -> None:
    class View:
        reads = 0

        def status(self) -> dict[str, Any]:
            self.reads += 1
            if 1 < self.reads <= 1 + failed_polls:
                raise _storage_unavailable()
            done = self.reads > 1
            return {
                "snapshot_version": 1,
                "state": "exited" if done else "running",
                "exit_code": 0 if done else None,
                "console_lines": 2 if done else 1,
                "console_tail": "one\ntwo\n" if done else "one\n",
            }

    monkeypatch.setattr(gcp, "_BatchJobStateReader", _RunningBatchState, raising=False)
    out = MagicMock()
    gcp._print_live_logs(
        [_job("RUNNING")],
        out=out,
        limit=10,
        follow=True,
        view_factory=lambda _: View(),
        sleep=_bounded_sleep(failed_polls + 1),
    )
    assert [call.args[0] for call in out.data.call_args_list] == ["one", "two"]
    assert out.progress.call_args_list[-1].args[0] == "Every followed job has exited."


def test_repeated_live_view_read_failures_stop_with_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class View:
        reads = 0

        def status(self) -> dict[str, Any]:
            self.reads += 1
            if self.reads > 1:
                raise _storage_unavailable()
            return {
                "snapshot_version": 1,
                "state": "running",
                "console_lines": 0,
                "console_tail": "",
            }

    monkeypatch.setattr(gcp, "_BatchJobStateReader", _RunningBatchState, raising=False)
    with pytest.raises(CLIError, match="live view of .* after 10 polls"):
        gcp._print_live_logs(
            [_job("RUNNING")],
            out=MagicMock(),
            limit=10,
            follow=True,
            view_factory=lambda _: View(),
            sleep=_bounded_sleep(10),
        )


@pytest.mark.parametrize("follow", [False, True])
def test_an_unreadable_first_live_view_read_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch, follow: bool
) -> None:
    class View:
        reads = 0

        def status(self) -> dict[str, Any]:
            self.reads += 1
            if self.reads == 1:
                raise _storage_unavailable()
            return {
                "snapshot_version": 1,
                "state": "exited",
                "exit_code": 0,
                "console_lines": 1,
                "console_tail": "only\n",
            }

    monkeypatch.setattr(gcp, "_BatchJobStateReader", _RunningBatchState, raising=False)
    out = MagicMock()
    gcp._print_live_logs(
        [_job("RUNNING")],
        out=out,
        limit=10,
        follow=follow,
        view_factory=lambda _: View(),
        sleep=_bounded_sleep(1),
    )
    messages = [call.args[0] for call in out.progress.call_args_list]
    assert messages[0].startswith("live view unreadable: ")
    assert "storage unavailable" in messages[0]
    printed = [call.args[0] for call in out.data.call_args_list]
    if follow:
        assert printed == ["only"]
        assert messages[-1] == "Every followed job has exited."
    else:
        assert printed == [] and len(messages) == 1


def test_an_unsupported_snapshot_version_is_an_operator_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = json.dumps({"snapshot_version": 2, "state": "running"}).encode()

    class Blob:
        def __init__(self, name: str) -> None:
            self.name = name

        def exists(self, **kwargs: Any) -> bool:
            return self.name.endswith(console.LIVE_STATUS_NAME)

        def download_as_bytes(self, *, start: int, end: int, **kwargs: Any) -> bytes:
            return record[start : end + 1]

    client = SimpleNamespace(bucket=lambda _: SimpleNamespace(blob=Blob))
    view = gcp_run_live.LiveView("gs://offline/live", client_factory=lambda: client)
    with pytest.raises(CLIError, match="unsupported live snapshot version 2; upgrade metaproc"):
        view.status()
    monkeypatch.setattr(gcp, "_BatchJobStateReader", _RunningBatchState, raising=False)
    with pytest.raises(CLIError, match="unsupported live snapshot version 2"):
        gcp._print_live_logs(
            [_job("RUNNING")],
            out=MagicMock(),
            limit=10,
            follow=True,
            view_factory=lambda _: view,
            sleep=_bounded_sleep(0),
        )


def test_wrapper_exit_stops_a_watched_status_render_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_file = tmp_path / "render.pid"
    # Stands in for the interpreter that runs `metaproc status`: it records its PID and
    # outlives the workload unless the wrapper stops it.
    fake_python = tmp_path / "fake-python"
    fake_python.write_text(f'#!/bin/sh\necho $$ > "{pid_file}"\nexec sleep 30\n')
    fake_python.chmod(0o755)
    # The workload exits only after the render has started.
    workload = [
        sys.executable,
        "-c",
        textwrap.dedent(
            f"""
            import os, time
            deadline = time.monotonic() + 10
            while not os.path.exists({str(pid_file)!r}) and time.monotonic() < deadline:
                time.sleep(0.01)
            time.sleep(0.1)
            """
        ),
    ]
    monkeypatch.setattr(console, "FINALIZE_GRACE_SECONDS", 0.1, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_python))
    settings = console.ConsoleSettings(
        console_path=tmp_path / "console.log",
        heartbeat_s=0.05,
        watch_dirs=(tmp_path / "run-a",),
    )
    pid = 0
    try:
        code = console.run_with_console(workload, settings, sink=Sink(), stdout=io.StringIO())
        assert code == 0
        assert pid_file.exists(), "the watched-status render never started"
        pid = int(pid_file.read_text())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            pytest.fail("the watched-status render outlived the wrapper")
    finally:
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
