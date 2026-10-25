"""Read a ``metaproc gcp run`` job's live view: its console tail, status and run status.

A job whose console goes to a file (``gcp_run_console``) keeps a small live view in the
dispatch bucket, because a laptop need not mount the job's storage. This module finds that view
from the job's own Batch spec and reads it, so ``metaproc gcp logs`` and
``metaproc gcp status`` show what the job is doing without Cloud Logging.
"""

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


@dataclass(frozen=True)
class LiveView:
    """One job's live view; every read returns ``None`` until the job has written it."""

    uri: str
    client_factory: Callable[[], Any] = _default_client

    def _bucket(self) -> tuple[Any, str]:
        bucket, prefix = _split(self.uri)
        return self.client_factory().bucket(bucket), prefix

    def _read(self, name: str) -> str | None:
        bucket, prefix = self._bucket()
        blob = bucket.blob(f"{prefix}/{name}" if prefix else name)
        if not blob.exists():
            return None
        return blob.download_as_text()

    def status(self) -> dict[str, Any] | None:
        text = self._read(LIVE_STATUS_NAME)
        return json.loads(text) if text else None

    def tail(self) -> str | None:
        return self._read(LIVE_TAIL_NAME)

    def run_statuses(self) -> dict[str, str]:
        bucket, prefix = self._bucket()
        runs_prefix = f"{prefix}/{LIVE_RUN_STATUS_DIR}/" if prefix else f"{LIVE_RUN_STATUS_DIR}/"
        statuses: dict[str, str] = {}
        for blob in bucket.list_blobs(prefix=runs_prefix):
            name = blob.name.removeprefix(runs_prefix).removesuffix(".txt")
            statuses[name] = blob.download_as_text()
        return dict(sorted(statuses.items()))


def summarize_status(status: dict[str, Any]) -> str:
    """One line for ``metaproc gcp status``: state, console size and freshness."""
    state = status.get("state", "unknown")
    if state == "exited":
        state = f"exited {status.get('exit_code')}"
    lines = status.get("console_lines")
    size = f"{lines:,} lines" if isinstance(lines, int) else "size unknown"
    updated = status.get("updated_at") or status.get("started_at") or "never"
    return f"{state}; console {status.get('console_path', '?')} ({size}); updated {updated}"


def new_tail_lines(tail: str, previous_total: int, total: int) -> tuple[list[str], int]:
    """The tail lines a follower has not printed yet, and how many it cannot show.

    ``total`` is the console's line count when the tail was written. When more lines
    arrived since the last poll than the tail holds, the follower shows the whole tail
    and reports the gap, which the console file still holds in full.
    """
    lines = tail.splitlines()
    fresh = max(0, total - previous_total)
    if fresh <= len(lines):
        return (lines[len(lines) - fresh :] if fresh else []), 0
    return lines, fresh - len(lines)
