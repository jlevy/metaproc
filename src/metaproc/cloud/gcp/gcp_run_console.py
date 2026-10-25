"""Run one ``metaproc gcp run`` command with its console in a file.

A local run prints its console to the operator's terminal and keeps its records in the
run tree. On GCP the job's run tree lives on a persistent run-storage mount (in a
Filestore deployment, the Filestore share); this wrapper appends the console there too,
so a job's complete output is a file like any other run record. Cloud Logging receives
only this wrapper's start line, a heartbeat and the exit line, besides the bootstrap
output written before the wrapper starts.

A machine that does not mount the job's storage reads the job through a small live view
in GCS. At every heartbeat and once at exit the wrapper rewrites the console's last lines
and a status record; it renders the ``metaproc status`` of each run directory the
dispatcher named every ``DEFAULT_RUN_STATUS_SECONDS`` (five minutes) and once at exit.
The view is a monitoring copy; the console file and the run tree remain the record.

The dispatcher runs ``python -m metaproc.cloud.gcp.gcp_run_console -- <cmd>`` and sets:

    METAPROC_GCP_RUN_CONSOLE_PATH  file the console is appended to, on the run-storage mount
    METAPROC_GCP_RUN_LIVE_GCS      optional gs:// prefix of the live view
    METAPROC_GCP_RUN_WATCH         optional comma-separated run directories
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Protocol, TextIO

CONSOLE_PATH_ENV = "METAPROC_GCP_RUN_CONSOLE_PATH"
LIVE_GCS_ENV = "METAPROC_GCP_RUN_LIVE_GCS"
WATCH_ENV = "METAPROC_GCP_RUN_WATCH"

CONSOLE_MODULE = "metaproc.cloud.gcp.gcp_run_console"
LOG_PREFIX = "[gcp-run-console]"

# Names of the live view's objects under METAPROC_GCP_RUN_LIVE_GCS.
LIVE_STATUS_NAME = "status.json"
LIVE_TAIL_NAME = "console-tail.txt"
LIVE_RUN_STATUS_DIR = "runs"

DEFAULT_HEARTBEAT_SECONDS = 60.0
# `metaproc status` of a large run takes a minute or more, so watched runs refresh less
# often than the heartbeat, and always once at exit.
DEFAULT_RUN_STATUS_SECONDS = 300.0
DEFAULT_TAIL_LINES = 400
RUN_STATUS_TIMEOUT_SECONDS = 120.0
_TAIL_READ_CHUNK = 64 * 1024


class LiveSink(Protocol):
    """Where the live view goes: one text object per name."""

    def put(self, name: str, text: str, content_type: str) -> None: ...


class GcsLiveSink:
    """Write live-view objects under a ``gs://bucket/prefix`` URI."""

    def __init__(self, uri: str) -> None:
        if not uri.startswith("gs://"):
            raise ValueError(f"{LIVE_GCS_ENV} must be a gs:// URI: {uri!r}")
        bucket, _, prefix = uri.removeprefix("gs://").partition("/")
        if not bucket:
            raise ValueError(f"{LIVE_GCS_ENV} names no bucket: {uri!r}")
        from google.cloud import storage  # noqa: PLC0415 -- optional [gcp-batch] dependency

        self._bucket = storage.Client().bucket(bucket)
        self._prefix = prefix.strip("/")

    def put(self, name: str, text: str, content_type: str) -> None:
        path = f"{self._prefix}/{name}" if self._prefix else name
        self._bucket.blob(path).upload_from_string(text, content_type=content_type, timeout=30)


@dataclass(frozen=True)
class ConsoleSettings:
    console_path: Path
    live_gcs: str = ""
    watch_dirs: tuple[Path, ...] = ()
    heartbeat_s: float = DEFAULT_HEARTBEAT_SECONDS
    tail_lines: int = DEFAULT_TAIL_LINES
    run_status_s: float = DEFAULT_RUN_STATUS_SECONDS

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ConsoleSettings:
        console_path = env.get(CONSOLE_PATH_ENV, "").strip()
        if not console_path:
            raise ValueError(f"{CONSOLE_PATH_ENV} is required")
        watch = tuple(Path(p) for p in env.get(WATCH_ENV, "").split(",") if p.strip())
        return cls(
            console_path=Path(console_path),
            live_gcs=env.get(LIVE_GCS_ENV, "").strip(),
            watch_dirs=watch,
        )


class ConsoleCounter:
    """Count the console's bytes and lines incrementally, reading only what is new."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.bytes = 0
        self.lines = 0

    def refresh(self) -> None:
        with self.path.open("rb") as handle:
            handle.seek(self.bytes)
            while chunk := handle.read(_TAIL_READ_CHUNK):
                self.bytes += len(chunk)
                self.lines += chunk.count(b"\n")


def read_tail(path: Path, lines: int) -> str:
    """The last ``lines`` lines of ``path``, read backward from its end."""
    if lines <= 0:
        return ""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        end = position = handle.tell()
        data = b""
        while position > 0 and data.count(b"\n") <= lines:
            step = min(_TAIL_READ_CHUNK, position)
            position -= step
            handle.seek(position)
            data = handle.read(step) + data
        del end
    tail = data.splitlines(keepends=True)[-lines:]
    return b"".join(tail).decode("utf-8", errors="replace")


def render_run_status(run_dir: Path) -> str:
    """``metaproc status`` for one run directory, as the operator would see it locally."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "metaproc", "status", str(run_dir)],
            capture_output=True,
            text=True,
            timeout=RUN_STATUS_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"metaproc status {run_dir} timed out after {RUN_STATUS_TIMEOUT_SECONDS:.0f}s\n"
    text = result.stdout
    if result.returncode != 0:
        text += f"\nmetaproc status exited {result.returncode}\n{result.stderr}"
    return text


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _exit_code(returncode: int) -> int:
    """A shell-style exit code: a child killed by signal N exits 128 + N."""
    return 128 - returncode if returncode < 0 else returncode


class LiveView:
    """Publish the status record, console tail and watched run status; never fail the job."""

    def __init__(
        self,
        settings: ConsoleSettings,
        sink: LiveSink | None,
        argv: Sequence[str],
        stdout: TextIO,
        run_status: Callable[[Path], str] = render_run_status,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._sink = sink
        self._stdout = stdout
        self._run_status = run_status
        self._clock = clock
        self._run_status_at: float | None = None
        self._failures = 0
        self.record: dict[str, object] = {
            "console_path": str(settings.console_path),
            "argv": list(argv),
            "host": socket.gethostname(),
            "batch_job_id": os.environ.get("BATCH_JOB_ID", ""),
            "started_at": _utc_now(),
            "state": "running",
            "exit_code": None,
            "watch": [str(p) for p in settings.watch_dirs],
        }

    def publish(self, counter: ConsoleCounter, *, final: bool = False) -> None:
        if self._sink is None:
            return
        now = self._clock()
        refresh_runs = (
            final
            or self._run_status_at is None
            or now - self._run_status_at >= self._settings.run_status_s
        )
        self.record.update(
            updated_at=_utc_now(), console_bytes=counter.bytes, console_lines=counter.lines
        )
        try:
            self._sink.put(
                LIVE_TAIL_NAME,
                read_tail(self._settings.console_path, self._settings.tail_lines),
                "text/plain; charset=utf-8",
            )
            for run_dir in self._settings.watch_dirs if refresh_runs else ():
                self._sink.put(
                    f"{LIVE_RUN_STATUS_DIR}/{run_dir.name}.txt",
                    self._run_status(run_dir),
                    "text/plain; charset=utf-8",
                )
            # Last, so a reader that sees the record's counts also sees a tail at least as new.
            self._sink.put(
                LIVE_STATUS_NAME,
                json.dumps(self.record, indent=2) + "\n",
                "application/json",
            )
            if refresh_runs:
                self._run_status_at = now
        except Exception as exc:  # noqa: BLE001 -- the live view is a monitoring copy
            self._failures += 1
            if self._failures == 1 or self._failures % 10 == 0:
                print(
                    f"{LOG_PREFIX} live view update failed ({self._failures}): {exc}",
                    file=self._stdout,
                    flush=True,
                )


def run_with_console(
    argv: Sequence[str],
    settings: ConsoleSettings,
    *,
    sink: LiveSink | None,
    stdout: TextIO,
    run_status: Callable[[Path], str] = render_run_status,
) -> int:
    """Run ``argv`` with its stdout and stderr appended to the console file."""
    console = settings.console_path
    console.parent.mkdir(parents=True, exist_ok=True)
    live = LiveView(settings, sink, argv, stdout, run_status)
    started = time.monotonic()
    with console.open("ab") as handle:
        # One header line, whatever the argv holds.
        command = shlex.join(argv).replace("\n", "\\n")
        handle.write(f"== {_utc_now()} {command}\n".encode())
        handle.flush()
        live_target = settings.live_gcs or "none"
        print(f"{LOG_PREFIX} console {console}; live view {live_target}", file=stdout, flush=True)
        # Its own session, so a forwarded signal reaches every process the command starts.
        child = subprocess.Popen(  # noqa: S603 -- the dispatcher's argv, as execvp would run it
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def forward(signum: int, _frame: FrameType | None) -> None:
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass

    previous = {sig: signal.signal(sig, forward) for sig in (signal.SIGTERM, signal.SIGINT)}
    counter = ConsoleCounter(console)
    try:
        while True:
            try:
                returncode = child.wait(timeout=settings.heartbeat_s)
                break
            except subprocess.TimeoutExpired:
                counter.refresh()
                elapsed = int(time.monotonic() - started)
                print(
                    f"{LOG_PREFIX} running {elapsed // 3600:d}:{elapsed % 3600 // 60:02d}:"
                    f"{elapsed % 60:02d}, console {counter.bytes:,} bytes, {counter.lines:,} lines",
                    file=stdout,
                    flush=True,
                )
                live.publish(counter)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)

    code = _exit_code(returncode)
    counter.refresh()
    live.record.update(state="exited", exit_code=code)
    live.publish(counter, final=True)
    print(
        f"{LOG_PREFIX} exit {code}; console {counter.bytes:,} bytes, {counter.lines:,} lines "
        f"at {console}",
        file=stdout,
        flush=True,
    )
    return code


def main(args: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if args is None else args)
    if argv[:1] == ["--"]:
        argv = argv[1:]
    if not argv:
        print(f"{LOG_PREFIX} no command given", file=sys.stderr)
        return 2
    try:
        settings = ConsoleSettings.from_env(os.environ)
    except ValueError as exc:
        print(f"{LOG_PREFIX} {exc}", file=sys.stderr)
        return 2
    sink: LiveSink | None = None
    if settings.live_gcs:
        try:
            sink = GcsLiveSink(settings.live_gcs)
        except Exception as exc:  # noqa: BLE001 -- run without a live view rather than not at all
            print(f"{LOG_PREFIX} no live view: {exc}", file=sys.stdout, flush=True)
    return run_with_console(argv, settings, sink=sink, stdout=sys.stdout)


if __name__ == "__main__":
    sys.exit(main())
