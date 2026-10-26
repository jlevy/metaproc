"""Read atomic file-console snapshots, with best-effort support for deployed legacy writers."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from metaproc.cloud.gcp.gcp_run_console import (
    LIVE_GCS_ENV,
    LIVE_RUN_STATUS_DIR,
    LIVE_STATUS_NAME,
    LIVE_TAIL_NAME,
)
from metaproc.cloud.gcp.gcp_run_logs import TERMINAL_STATES
from metaproc.errors import CLIError
from metaproc.models.gcp_console_live import (
    LIVE_SNAPSHOT_VERSION,
    MAX_LIVE_OBJECT_BYTES,
    MAX_RUN_STATUS_BYTES,
    MAX_TAIL_BYTES,
    ConsoleLiveSnapshot,
)

READ_REQUEST_SECONDS = 10.0
LEGACY_RUN_STATUS_HEADER_BYTES = 256


def live_view_uri_of(job: Any) -> str:
    """The live view URI a Batch job was dispatched with, or ``""``."""
    for group in getattr(job, "task_groups", None) or ():
        for runnable in getattr(group.task_spec, "runnables", None) or ():
            environment = getattr(runnable, "environment", None)
            variables = getattr(environment, "variables", None) or {}
            uri = dict(variables).get(LIVE_GCS_ENV, "")
            if uri:
                return str(uri)
    return ""


def _split(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(f"not a gs:// URI: {uri!r}")
    bucket, _, prefix = uri.removeprefix("gs://").partition("/")
    return bucket, prefix.strip("/")


def _default_client() -> Any:
    from google.cloud import storage  # noqa: PLC0415 -- optional [gcp-batch] dependency

    return storage.Client()


def _read_blob(blob: Any, max_bytes: int) -> str:
    # Inclusive end requests one extra byte, enough to reject oversize objects without
    # downloading the remainder. Decode only after checking the byte bound.
    raw: bytes = blob.download_as_bytes(
        start=0, end=max_bytes, raw_download=True, timeout=READ_REQUEST_SECONDS, retry=None
    )
    if len(raw) > max_bytes:
        raise ValueError(
            f"live object exceeds its {max_bytes:,}-byte read limit; use the console file"
        )
    return raw.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class LiveView:
    """One job's monitoring copy; missing objects return ``None``.

    Each read is one bounded request without SDK retries, so a read can fail on a
    transient storage error. A caller that follows the view retries on its next poll.
    """

    uri: str
    client_factory: Callable[[], Any] = _default_client

    def _bucket(self) -> tuple[Any, str]:
        bucket, prefix = _split(self.uri)
        return self.client_factory().bucket(bucket), prefix

    def _read(self, name: str, max_bytes: int = MAX_LIVE_OBJECT_BYTES) -> str | None:
        bucket, prefix = self._bucket()
        blob = bucket.blob(f"{prefix}/{name}" if prefix else name)
        if not blob.exists(timeout=READ_REQUEST_SECONDS, retry=None):
            return None
        return _read_blob(blob, max_bytes)

    def status(self) -> dict[str, Any] | None:
        text = self._read(LIVE_STATUS_NAME)
        if not text:
            return None
        record = json.loads(text)
        if not isinstance(record, dict):
            raise ValueError("live status must be a JSON object")
        version = record.get("snapshot_version")
        if version is not None:
            if version != LIVE_SNAPSHOT_VERSION:
                # Persistent, unlike a failed read: a newer writer published this view.
                raise CLIError(
                    f"unsupported live snapshot version {version!r}; upgrade metaproc to read it"
                )
            return ConsoleLiveSnapshot.model_validate(record).model_dump(mode="json", by_alias=True)
        return record

    def tail(self) -> str | None:
        record = self.status()
        if record and record.get("snapshot_version") == LIVE_SNAPSHOT_VERSION:
            return str(record["console_tail"])
        return self._read(LIVE_TAIL_NAME, MAX_TAIL_BYTES)

    def run_statuses(self) -> dict[str, str]:
        record = self.status()
        if record and record.get("snapshot_version") == LIVE_SNAPSHOT_VERSION:
            return {
                name: (
                    f"Updated: {status['updated_at']}\n{status['text']}"
                    if status["updated_at"] is not None
                    else "No watched status cached yet.\n"
                )
                for name, status in sorted(record["watch_status"].items())
            }
        bucket, prefix = self._bucket()
        runs_prefix = f"{prefix}/{LIVE_RUN_STATUS_DIR}/" if prefix else f"{LIVE_RUN_STATUS_DIR}/"
        statuses: dict[str, str] = {}
        for blob in bucket.list_blobs(prefix=runs_prefix, timeout=READ_REQUEST_SECONDS, retry=None):
            name = blob.name.removeprefix(runs_prefix).removesuffix(".txt")
            statuses[name] = _read_blob(blob, MAX_RUN_STATUS_BYTES + LEGACY_RUN_STATUS_HEADER_BYTES)
        return dict(sorted(statuses.items()))


def summarize_status(status: dict[str, Any], batch_state: str | None = None) -> str:
    """State and freshness, with authoritative Batch outcomes for incomplete live records."""
    state = status.get("state", "unknown")
    if state == "exited":
        state = f"exited {status.get('exit_code')}"
        if batch_state in TERMINAL_STATES:
            state += f" (Batch {batch_state})"
    elif batch_state in TERMINAL_STATES:
        state = f"lost (Batch {batch_state})"
    lines = status.get("console_lines")
    size = f"{lines:,} lines" if isinstance(lines, int) else "size unknown"
    updated = status.get("updated_at") or status.get("started_at") or "never"
    return f"{state}; console {status.get('console_path', '?')} ({size}); updated {updated}"


def describe_missing_view(job_name: str, batch_state: str | None = None) -> str:
    """Explain a missing monitoring copy in the context of Batch's current state."""
    if batch_state in TERMINAL_STATES:
        return (
            f"no live view; Batch {batch_state} before a live snapshot was available "
            f"(`metaproc gcp logs {job_name} --cloud-logging --include-agent-logs` shows bootstrap logs)"
        )
    return "no live view yet"


def tail_lines(tail: str) -> list[str]:
    """Split on the newline counted by the writer; carriage returns do not advance cursors."""
    lines = tail.split("\n")
    return lines[:-1] if lines[-1] == "" else lines


def new_tail_lines(tail: str, previous_total: int, total: int) -> tuple[list[str], int]:
    """New logical lines and the number omitted by the bounded monitoring copy.

    A byte-truncated giant line is one marked suffix of that logical line. A running
    writer publishes only complete lines; at exit its last partial line counts once.
    Legacy writers have no atomic pair, so their cursor pairing is best effort.
    """
    lines = tail_lines(tail)
    fresh = max(0, total - previous_total)
    if fresh <= len(lines):
        return (lines[len(lines) - fresh :] if fresh else []), 0
    return lines, fresh - len(lines)
