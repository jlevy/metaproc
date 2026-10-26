"""Run one ``metaproc gcp run`` command with its console in a persistent file.

The console file is the complete record. Cloud Logging receives the wrapper's start,
heartbeat and exit lines, plus bootstrap output from before the wrapper starts.
A bounded GCS monitoring snapshot carries the tail and its count in one atomic object.
One daemon publishes coalesced snapshots; another caches watched-run status, refreshed
at most every five minutes. Neither renderer nor network I/O blocks child supervision.
The wrapper gives final publication a bounded grace period, never waits for a fresh
watched-run render at exit, and stops a render that is still running.

The dispatcher runs ``python -m metaproc.cloud.gcp.gcp_run_console -- <cmd>`` and sets:

    METAPROC_GCP_RUN_CONSOLE_PATH  persistent console file, opened for append
    METAPROC_GCP_RUN_LIVE_GCS      optional gs:// prefix of the live view
    METAPROC_GCP_RUN_WATCH         optional comma-separated run directories
"""

from __future__ import annotations

import os
import posixpath
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Any, Protocol, TextIO

from metaproc.models.gcp_console_live import (
    MAX_LIVE_OBJECT_BYTES,
    MAX_RUN_STATUS_BYTES,
    MAX_TAIL_BYTES,
    ConsoleLiveSnapshot,
    WatchedRunSnapshot,
)

CONSOLE_PATH_ENV = "METAPROC_GCP_RUN_CONSOLE_PATH"
LIVE_GCS_ENV = "METAPROC_GCP_RUN_LIVE_GCS"
WATCH_ENV = "METAPROC_GCP_RUN_WATCH"
CONSOLE_MODULE = "metaproc.cloud.gcp.gcp_run_console"
LOG_PREFIX = "[gcp-run-console]"
LIVE_STATUS_NAME = "status.json"
LIVE_TAIL_NAME = "console-tail.txt"
LIVE_RUN_STATUS_DIR = "runs"
DEFAULT_HEARTBEAT_SECONDS = 60.0
DEFAULT_RUN_STATUS_SECONDS = 300.0
DEFAULT_TAIL_LINES = 400
RUN_STATUS_TIMEOUT_SECONDS = 120.0
UPLOAD_REQUEST_SECONDS = 5.0
PUBLICATION_BUDGET_SECONDS = 30.0
FINALIZE_GRACE_SECONDS = 2.0
_TAIL_READ_CHUNK = 64 * 1024


class LiveSink(Protocol):
    """Where the live view goes: one text object per name."""

    def put(self, name: str, text: str, content_type: str) -> None: ...


class GcsLiveSink:
    """Write live-view objects with no implicit retries or unbounded retry deadline."""

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
        blob: Any = self._bucket.blob(path)
        blob.upload_from_string(
            text, content_type=content_type, timeout=UPLOAD_REQUEST_SECONDS, retry=None
        )


def normalize_watch_dirs(paths: Sequence[str | Path]) -> tuple[Path, ...]:
    """Keep stable basename keys; reject collisions before dispatch or execution.

    These are container paths, so normalization is lexical POSIX normalization, without
    following symlinks or resolving them against the launcher's filesystem.
    """
    names: dict[str, Path] = {}
    for value in paths:
        path = Path(posixpath.normpath(str(value)))
        previous = names.get(path.name)
        if previous is not None and previous != path:
            raise ValueError(
                f"watched paths have the same basename {path.name!r}: {previous}, {path}"
            )
        names[path.name] = path
    return tuple(names.values())


@dataclass(frozen=True)
class ConsoleSettings:
    console_path: Path
    live_gcs: str = ""
    watch_dirs: tuple[Path, ...] = ()
    heartbeat_s: float = DEFAULT_HEARTBEAT_SECONDS
    tail_lines: int = DEFAULT_TAIL_LINES
    run_status_s: float = DEFAULT_RUN_STATUS_SECONDS

    def __post_init__(self) -> None:
        object.__setattr__(self, "watch_dirs", normalize_watch_dirs(self.watch_dirs))
        if self.heartbeat_s <= 0 or self.run_status_s <= 0:
            raise ValueError("heartbeat_s and run_status_s must be positive")

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ConsoleSettings:
        console_path = env.get(CONSOLE_PATH_ENV, "").strip()
        if not console_path:
            raise ValueError(f"{CONSOLE_PATH_ENV} is required")
        return cls(
            console_path=Path(console_path),
            live_gcs=env.get(LIVE_GCS_ENV, "").strip(),
            watch_dirs=tuple(
                Path(p.strip()) for p in env.get(WATCH_ENV, "").split(",") if p.strip()
            ),
        )


class ConsoleCounter:
    """Count each appended byte once, retaining the last complete-line boundary."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.bytes = 0
        self.lines = 0
        self.complete_bytes = 0

    def refresh(self) -> None:
        with self.path.open("rb") as handle:
            # Capture one EOF; a writer appending continuously cannot extend this scan.
            end = os.fstat(handle.fileno()).st_size
            handle.seek(self.bytes)
            while self.bytes < end:
                chunk = handle.read(min(_TAIL_READ_CHUNK, end - self.bytes))
                if not chunk:
                    break
                last = chunk.rfind(b"\n")
                if last >= 0:
                    self.complete_bytes = self.bytes + last + 1
                self.bytes += len(chunk)
                self.lines += chunk.count(b"\n")


@dataclass(frozen=True)
class ConsoleTail:
    text: str
    truncated: bool = False
    start_partial: bool = False


def _tail_snapshot(path: Path, lines: int, end: int | None = None) -> ConsoleTail:
    """Read at most 256 KiB once; a giant logical line contributes a marked suffix."""
    if lines <= 0:
        return ConsoleTail("")
    if end is not None and end < 0:
        raise ValueError("console snapshot byte boundary must be nonnegative")
    with path.open("rb") as handle:
        if end is None:
            end = handle.seek(0, os.SEEK_END)
        start = max(0, end - MAX_TAIL_BYTES)
        handle.seek(start)
        data = handle.read(end - start)
    parts = data.split(b"\n")
    terminated = bool(data) and parts[-1] == b""
    if terminated:
        parts.pop()
    selected = parts[-lines:]
    partial = start > 0 and len(selected) == len(parts)
    if partial and len(selected) > 1:
        # The byte limit cut an older line; retain only the following complete lines.
        selected = selected[1:]
        partial = False
    raw = b"\n".join(selected) + (b"\n" if terminated and selected else b"")
    if partial:
        # A valid UTF-8 line may be cut inside its first codepoint.
        raw = raw.lstrip(bytes(range(0x80, 0xC0)))
    text = raw.decode("utf-8", errors="replace")
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_TAIL_BYTES:
        text = encoded[-MAX_TAIL_BYTES:].decode("utf-8", errors="ignore")
        partial = True
    return ConsoleTail(text, start > 0 or len(parts) > lines, partial)


def read_tail(path: Path, lines: int, end: int | None = None) -> str:
    """Last ``lines`` newline-delimited records, capped at 256 KiB of scan and output."""
    return _tail_snapshot(path, lines, end).text


class RunStatusRenderer:
    """Render watched runs with ``metaproc status``; ``cancel`` stops a render in flight.

    Each render has a time limit and a 64 KiB captured-output limit. After ``cancel``
    no render starts, so an exiting wrapper leaves no ``metaproc status`` behind.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._cancelled = False

    def __call__(self, run_dir: Path) -> str:
        # Private staging prevents a pipe from retaining unbounded output in RAM.
        with tempfile.TemporaryFile() as output:
            with self._lock:
                if self._cancelled:
                    return f"metaproc status {run_dir} not started: the wrapper is exiting\n"
                process = subprocess.Popen(  # noqa: S603 -- this interpreter, fixed argv
                    [sys.executable, "-m", "metaproc", "status", str(run_dir)],
                    stdout=output,
                    stderr=subprocess.STDOUT,
                )
                self._process = process
            try:
                returncode = process.wait(timeout=RUN_STATUS_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                return (
                    f"metaproc status {run_dir} timed out after {RUN_STATUS_TIMEOUT_SECONDS:.0f}s\n"
                )
            finally:
                with self._lock:
                    self._process = None
            output.seek(0)
            raw = output.read(MAX_RUN_STATUS_BYTES)
            text = raw.decode("utf-8", errors="replace")
            if output.read(1):
                text += "\n[watched status truncated]\n"
            if returncode != 0:
                text += f"\nmetaproc status exited {returncode}\n"
            return text

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            process = self._process
        if process is not None:
            # Popen.kill skips a process it has already reaped.
            process.kill()


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _exit_code(returncode: int) -> int:
    """A shell-style exit code: a child killed by signal N exits 128 + N."""
    return 128 - returncode if returncode < 0 else returncode


class _WatchedRuns:
    """One serial renderer, cached results, and no queue of stale refresh requests."""

    def __init__(self, settings: ConsoleSettings, render: Callable[[Path], str]) -> None:
        self._settings = settings
        self._render = render
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None
        self._busy = False
        self._next_at = 0.0
        self._cached = {p.name: WatchedRunSnapshot(path=str(p)) for p in settings.watch_dirs}

    def snapshot(self, now: float, *, refresh: bool) -> dict[str, WatchedRunSnapshot]:
        with self._lock:
            if refresh and self._cached and not self._busy and now >= self._next_at:
                self._busy = True
                self._next_at = now + self._settings.run_status_s
                if self._thread is None:
                    self._thread = threading.Thread(
                        target=self._work, name="gcp-console-render", daemon=True
                    )
                    self._thread.start()
                self._ready.set()
            return dict(self._cached)

    def _work(self) -> None:
        while True:
            self._ready.wait()
            self._ready.clear()
            if self._closed.is_set():
                return
            for path in self._settings.watch_dirs:
                if self._closed.is_set():
                    return
                try:
                    text = self._render(path)
                except Exception as exc:  # noqa: BLE001 -- diagnostic monitoring copy
                    text = f"metaproc status {path} failed: {exc}\n"
                text = text.encode("utf-8")[:MAX_RUN_STATUS_BYTES].decode("utf-8", errors="ignore")
                status = WatchedRunSnapshot(path=str(path), text=text, updated_at=_utc_now())
                with self._lock:
                    self._cached[path.name] = status
            with self._lock:
                self._busy = False

    def close(self) -> None:
        self._closed.set()
        self._ready.set()
        if isinstance(self._render, RunStatusRenderer):
            self._render.cancel()


class LiveView:
    """Build consistent snapshots and publish monitoring copies without failing a job."""

    def __init__(
        self,
        settings: ConsoleSettings,
        sink: LiveSink | None,
        argv: Sequence[str],
        stdout: TextIO,
        run_status: Callable[[Path], str] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._sink = sink
        self._stdout = stdout
        self._clock = clock
        self._watched = _WatchedRuns(settings, run_status or RunStatusRenderer())
        self._published_runs: dict[str, WatchedRunSnapshot] = {}
        self._failures = 0
        self.record: dict[str, object] = {
            "console_path": str(settings.console_path),
            "argv": list(argv),
            "host": socket.gethostname(),
            "started_at": _utc_now(),
            "state": "running",
            "exit_code": None,
            "watch": [str(p) for p in settings.watch_dirs],
        }

    def snapshot(self, counter: ConsoleCounter, *, final: bool = False) -> ConsoleLiveSnapshot:
        end = counter.bytes if final else counter.complete_bytes
        tail = _tail_snapshot(self._settings.console_path, self._settings.tail_lines, end)
        return ConsoleLiveSnapshot.model_validate(
            {
                **self.record,
                "updated_at": _utc_now(),
                "console_bytes": counter.bytes,
                "console_complete_bytes": counter.complete_bytes,
                "console_lines": counter.lines
                + int(final and counter.bytes > counter.complete_bytes),
                "console_tail": tail.text,
                "tail_truncated": tail.truncated,
                "tail_start_partial": tail.start_partial,
                "watch_status": self._watched.snapshot(self._clock(), refresh=not final),
            }
        )

    def publish(self, counter: ConsoleCounter, *, final: bool = False) -> None:
        if self._sink is not None:
            try:
                self.publish_snapshot(self.snapshot(counter, final=final))
            except Exception as exc:  # noqa: BLE001 -- local snapshot is also monitoring
                self.report_failure(exc)

    def publish_snapshot(self, snapshot: ConsoleLiveSnapshot) -> None:
        sink = self._sink
        if sink is None:
            return
        started = time.monotonic()

        def put(name: str, text: str, content_type: str) -> None:
            if time.monotonic() - started >= PUBLICATION_BUDGET_SECONDS:
                raise TimeoutError("live publication exceeded its total time budget")
            if len(text.encode("utf-8")) > MAX_LIVE_OBJECT_BYTES:
                raise ValueError("live object exceeds its 4 MiB size limit")
            sink.put(name, text, content_type)

        try:
            text = snapshot.model_dump_json(indent=2, by_alias=True) + "\n"
            # Validate before replacing either compatibility object. Modern readers use
            # only status.json, so neither overwrite order can mix their generations.
            if len(text.encode("utf-8")) > MAX_LIVE_OBJECT_BYTES:
                raise ValueError("live snapshot exceeds its 4 MiB size limit")
            put(LIVE_TAIL_NAME, snapshot.console_tail, "text/plain; charset=utf-8")
            put(LIVE_STATUS_NAME, text, "application/json")
            for name, status in snapshot.watch_status.items():
                if status.updated_at is not None and self._published_runs.get(name) != status:
                    put(
                        f"{LIVE_RUN_STATUS_DIR}/{name}.txt",
                        f"Updated: {status.updated_at}\n{status.text}",
                        "text/plain; charset=utf-8",
                    )
                    self._published_runs[name] = status
        except Exception as exc:  # noqa: BLE001 -- the live view is a monitoring copy
            self.report_failure(exc)

    def report_failure(self, exc: Exception) -> None:
        self._failures += 1
        if self._failures == 1 or self._failures % 10 == 0:
            print(
                f"{LOG_PREFIX} live view update failed ({self._failures}): {exc}",
                file=self._stdout,
                flush=True,
            )

    def close(self) -> None:
        self._watched.close()


class _Publisher:
    """One daemon and one replaceable pending snapshot, regardless of sink latency."""

    def __init__(self, live: LiveView) -> None:
        self._live = live
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._finished = threading.Event()
        self._closed = threading.Event()
        self._pending: ConsoleLiveSnapshot | None = None
        self._thread = threading.Thread(target=self._work, name="gcp-console-publish", daemon=True)
        self._thread.start()

    def submit(self, counter: ConsoleCounter, *, final: bool = False) -> None:
        try:
            snapshot = self._live.snapshot(counter, final=final)
        except Exception as exc:  # noqa: BLE001 -- snapshot errors cannot kill the command
            self._live.report_failure(exc)
            if final:
                self._finished.set()
            return
        with self._lock:
            self._pending = snapshot
        self._ready.set()

    def _work(self) -> None:
        while True:
            self._ready.wait()
            self._ready.clear()
            if self._closed.is_set():
                return
            with self._lock:
                snapshot, self._pending = self._pending, None
            if snapshot is not None:
                self._live.publish_snapshot(snapshot)
                if snapshot.state == "exited":
                    self._finished.set()
                    return

    def finish(self, counter: ConsoleCounter) -> None:
        self.submit(counter, final=True)
        if not self._finished.wait(FINALIZE_GRACE_SECONDS):
            print(
                f"{LOG_PREFIX} final live update exceeded {FINALIZE_GRACE_SECONDS:g}s grace; "
                "Batch state and the console file remain authoritative",
                file=self._live._stdout,
                flush=True,
            )
        self.close()

    def close(self) -> None:
        self._closed.set()
        self._ready.set()
        self._live.close()


def run_with_console(
    argv: Sequence[str],
    settings: ConsoleSettings,
    *,
    sink: LiveSink | None,
    stdout: TextIO,
    run_status: Callable[[Path], str] | None = None,
) -> int:
    """Run ``argv`` with its stdout and stderr appended to the console file."""
    console = settings.console_path
    console.parent.mkdir(parents=True, exist_ok=True)
    live = LiveView(settings, sink, argv, stdout, run_status)
    started = time.monotonic()
    child: subprocess.Popen[bytes] | None = None
    held: list[int] = []
    previous: dict[int, int | Callable[[int, FrameType | None], object] | None] = {}
    publisher: _Publisher | None = None

    def forward(signum: int, _frame: FrameType | None) -> None:
        if child is None:
            held.append(signum)
            return
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass

    try:
        # Install before Popen creates the independent child process group.
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, forward)
        with console.open("ab") as handle:
            command = shlex.join(argv).replace("\n", "\\n")
            handle.write(f"== {_utc_now()} {command}\n".encode())
            handle.flush()
            print(
                f"{LOG_PREFIX} console {console}; live view {settings.live_gcs or 'none'}",
                file=stdout,
                flush=True,
            )
            child = subprocess.Popen(  # noqa: S603 -- dispatcher argv, as execvp would run it
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        for signum in held:
            forward(signum, None)
        counter = ConsoleCounter(console)
        if sink is not None:
            try:
                publisher = _Publisher(live)
            except RuntimeError as exc:
                live.report_failure(exc)
            if publisher is not None:
                counter.refresh()
                publisher.submit(counter)
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
                if publisher is not None:
                    publisher.submit(counter)
        code = _exit_code(returncode)
        counter.refresh()
        live.record.update(state="exited", exit_code=code)
        if publisher is not None:
            publisher.finish(counter)
        print(
            f"{LOG_PREFIX} exit {code}; console {counter.bytes:,} bytes, "
            f"{counter.lines + int(counter.bytes > counter.complete_bytes):,} lines at {console}",
            file=stdout,
            flush=True,
        )
        return code
    finally:
        if publisher is not None:
            publisher.close()
        else:
            live.close()
        if child is not None and child.poll() is None:
            # A setup/snapshot failure must not leave the independent group running.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


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
        except Exception as exc:  # noqa: BLE001 -- monitoring must not prevent execution
            print(f"{LOG_PREFIX} no live view: {exc}", file=sys.stdout, flush=True)
    return run_with_console(argv, settings, sink=sink, stdout=sys.stdout)


if __name__ == "__main__":
    sys.exit(main())
