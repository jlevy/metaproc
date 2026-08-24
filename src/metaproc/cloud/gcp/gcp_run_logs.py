"""Log tail + exit-code propagation for ``metaproc gcp run`` blocking mode.

Polls a GCP Batch job's state via :func:`BatchServiceClient.get_job` at a
fixed interval, reports state transitions, and streams matching Cloud Logging entries
(``labels.job_uid=<uid>``) with a ``[gcp-run]`` prefix, and returns a
unix exit code derived from the job's terminal state:

- ``SUCCEEDED`` → 0
- ``DELETION_IN_PROGRESS`` / ``CANCELLED`` → 130 (SIGINT)
- anything else terminal → 1

``--detach`` callers skip this entirely and use :func:`build_log_url`
to print a console URL instead.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

log = logging.getLogger(__name__)

LOG_PREFIX = "[gcp-run]"

# Batch states that mean "stop polling and return an exit code".
TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "DELETION_IN_PROGRESS"})

# Max consecutive get_job / log-fetch failures before we give up and return
# a non-zero exit code. Cloud Logging / Batch blips for a poll or two; a run
# of failures this long means something is actually broken (auth expired,
# network partition, quota exhausted) and we shouldn't loop forever.
MAX_CONSECUTIVE_ERRORS = 10


def _get_batch_v1() -> Any:
    """Indirection so tests can stub the Batch client."""
    from google.cloud import batch_v1  # noqa: PLC0415 -- optional [gcp-batch] dependency

    return batch_v1


def _get_logging_client(project: str) -> Any:
    """Return a Cloud Logging client for ``project`` (lazy + injectable)."""
    from google.cloud import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
        logging as cloud_logging,
    )

    return cloud_logging.Client(project=project)


def _state_to_exit_code(state: str) -> int:
    """Map a Batch terminal state to a unix exit code."""
    if state == "SUCCEEDED":
        return 0
    if state in {"CANCELLED", "DELETION_IN_PROGRESS"}:
        return 130
    return 1


def _job_id_from_resource_name(resource_name: str) -> str:
    """Extract the trailing job-id from a Batch job resource name."""
    return resource_name.rsplit("/", 1)[-1]


def _job_state_name(job: Any) -> str:
    """Read the State enum off a Batch Job; tolerate string / .name / Enum repr."""
    state = job.status.state
    name = getattr(state, "name", None)
    if name:
        return str(name)
    return str(state).rsplit(".", 1)[-1]


def _format_entry(entry: Any) -> str:
    """Render a Cloud Logging entry to a single line."""
    payload = entry.payload
    message = payload.get("message") or str(payload) if isinstance(payload, dict) else str(payload)
    timestamp = getattr(entry, "timestamp", "")
    return f"{timestamp} {message}".strip()


def _rfc3339_watermark(timestamp: object) -> str:
    """Normalize a Cloud Logging entry timestamp for the next query.

    ``google-cloud-logging`` returns ``datetime`` objects for real entries, while
    older tests and a few fakes use strings. ``str(datetime)`` emits a space between
    the date and time, which the Logging filter parser rejects. Preserve strings and
    serialize real datetimes as RFC3339 instead.
    """
    if isinstance(timestamp, datetime):
        normalized = timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=UTC)
        return normalized.isoformat().replace("+00:00", "Z")
    return str(timestamp or "")


def _fetch_log_entries(
    *,
    project: str,
    job_uid: str,
    job_id: str,
    since: str = "",
    client: Any | None = None,
) -> list[Any]:
    """List Cloud Logging entries scoped to a single Batch job.

    Returns entries sorted by timestamp ascending. The caller is responsible
    for deduping by ``insert_id`` across polls.

    ``since`` is an RFC3339 timestamp watermark — when set, the query only
    asks for entries with ``timestamp >= since``. Without this, every poll
    re-asks for the oldest 500 entries and long-running jobs lose the tail
    once total log volume exceeds 500.
    """
    effective_client: Any = client if client is not None else _get_logging_client(project)
    filter_parts = [
        'resource.type="batch.googleapis.com/Job"',
        f'labels."job_uid"="{job_uid}"'
        if job_uid
        else f'labels."batch.googleapis.com/job_id"="{job_id}"',
    ]
    if since:
        filter_parts.append(f'timestamp >= "{since}"')
    filter_str = " AND ".join(filter_parts)
    return list(
        effective_client.list_entries(
            filter_=filter_str,
            order_by="timestamp asc",
            max_results=500,
        )
    )


def tail_gcp_run_logs(
    *,
    job_resource_name: str,
    project: str,
    poll_interval_s: float = 2.0,
    out: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Tail Cloud Logging for ``job_resource_name`` until terminal; return exit code.

    Polls the Batch job's state every ``poll_interval_s`` seconds. State
    transitions and not-yet-seen log entries scoped by the job's UID (or job id,
    as a fallback before UID is available) are printed to ``out`` with a
    ``[gcp-run]`` prefix. One Cloud Logging client is reused and then closed.

    Returns the exit code derived from the terminal state.
    """
    batch_v1 = _get_batch_v1()
    client = batch_v1.BatchServiceClient()
    logging_client: Any | None = None
    job_id = _job_id_from_resource_name(job_resource_name)
    job_uid = ""
    seen_insert_ids: set[str] = set()
    watermark = ""
    state = ""
    previous_state = ""
    consecutive_errors = 0

    try:
        while state not in TERMINAL_STATES:
            sleep(poll_interval_s)
            request = batch_v1.GetJobRequest(name=job_resource_name)
            try:
                job = client.get_job(request)
            except Exception as exc:  # noqa: BLE001
                consecutive_errors += 1
                log.warning(
                    "get_job(%s) failed (%d/%d): %s",
                    job_resource_name,
                    consecutive_errors,
                    MAX_CONSECUTIVE_ERRORS,
                    exc,
                )
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    log.error(
                        "Giving up after %d consecutive get_job failures; exiting 1",
                        consecutive_errors,
                    )
                    return 1
                continue

            state = _job_state_name(job)
            if state != previous_state:
                out(f"{LOG_PREFIX} state={state}")
                previous_state = state
            if not job_uid:
                job_uid = str(getattr(job, "uid", "") or "")

            try:
                if logging_client is None:
                    logging_client = _get_logging_client(project)
                entries = _fetch_log_entries(
                    project=project,
                    job_uid=job_uid,
                    job_id=job_id,
                    since=watermark,
                    client=logging_client,
                )
                consecutive_errors = 0
            except Exception as exc:  # noqa: BLE001
                consecutive_errors += 1
                log.warning(
                    "Cloud Logging fetch failed (%d/%d): %s",
                    consecutive_errors,
                    MAX_CONSECUTIVE_ERRORS,
                    exc,
                )
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    log.error(
                        "Giving up after %d consecutive log-fetch failures; exiting 1",
                        consecutive_errors,
                    )
                    return 1
                entries = []

            for entry in entries:
                insert_id = getattr(entry, "insert_id", "")
                if insert_id and insert_id in seen_insert_ids:
                    continue
                if insert_id:
                    seen_insert_ids.add(insert_id)
                ts = _rfc3339_watermark(getattr(entry, "timestamp", ""))
                if ts and ts > watermark:
                    watermark = ts
                out(f"{LOG_PREFIX} {_format_entry(entry)}")

        return _state_to_exit_code(state)
    finally:
        if logging_client is not None:
            logging_client.close()


def build_log_url(job_resource_name: str) -> str:
    """Build a Cloud Logging console URL for a Batch job resource name.

    Useful for ``--detach`` callers that want a clickable URL without
    streaming logs.
    """
    parts = job_resource_name.split("/")
    # Format: projects/<project>/locations/<region>/jobs/<job-id>
    project = parts[1] if len(parts) >= 2 else ""
    job_id = parts[-1]
    query = (
        f'resource.type="batch.googleapis.com/Job" '
        f'AND labels."batch.googleapis.com/job_id"="{job_id}"'
    )

    return f"https://console.cloud.google.com/logs/query;query={quote(query)}?project={project}"
