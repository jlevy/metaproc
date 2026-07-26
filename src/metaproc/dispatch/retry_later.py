"""Retry-later checkpoint writer for auth-pool dispatches (P2c.3).

When ``RetryLaterPolicy.SIGNAL`` + ``select_fallback`` returning
``None`` converge, the dispatch writes a ``retry_later.yaml``
checkpoint, emits a ``retry_later`` event into the process event
stream, and exits with code 78. The resume daemon (P2c.5) picks up
the checkpoint at ``cooling_until_ts`` and re-dispatches.

The writer is deliberately small and single-purpose: no LLM in the
loop, no classification work here — that's already been done by
the adapter's ``classify_failure`` / the slot coordinator. This
module only handles serialization and the exit-78 protocol.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from strif import atomic_output_file

log = logging.getLogger(__name__)


# Exit code consumed by the resume daemon and wrapping scripts. Chosen
# per spec (Components §10); deliberately distinct from the usual
# 0/1/64/78 set so operators and CI can tell apart "pool deferred" from
# "bad usage" (64) or "unknown failure" (1).
RETRY_LATER_EXIT_CODE: int = 78


@dataclass(frozen=True)
class PoolSnapshotEntry:
    adapter: str
    label: str
    status: str
    cooling_until_ts: int | None


@dataclass(frozen=True)
class RetryLaterCheckpoint:
    """What the resume daemon needs to re-dispatch this step.

    Schema version 1. Additive fields only in future versions.
    Never includes OAuth blobs — only metadata.
    """

    schema_version: int = 1
    written_at_iso: str = ""
    written_at_ts: int = 0
    step_id: str = ""
    cooling_until_ts: int | None = None
    cooling_until_iso: str = ""
    from_step_arg: str = ""
    pool_snapshot: list[PoolSnapshotEntry] = field(default_factory=list)
    env_snapshot_ref: str = ""
    dispatch_argv: list[str] = field(default_factory=list)
    retries_attempted: int = 0
    max_retries: int = 3
    next_attempt_earliest_ts: int = 0

    @staticmethod
    def from_pool_state(
        *,
        step_id: str,
        cooling_until_ts: int | None,
        pool_snapshot: list[PoolSnapshotEntry],
        dispatch_argv: list[str],
        env_snapshot_ref: str = "",
        retries_attempted: int = 0,
        max_retries: int = 3,
    ) -> RetryLaterCheckpoint:
        now_ts = int(time.time())
        iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts))
        cooling_iso = (
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cooling_until_ts))
            if cooling_until_ts is not None
            else ""
        )
        return RetryLaterCheckpoint(
            written_at_iso=iso,
            written_at_ts=now_ts,
            step_id=step_id,
            cooling_until_ts=cooling_until_ts,
            cooling_until_iso=cooling_iso,
            from_step_arg=step_id,
            pool_snapshot=pool_snapshot,
            env_snapshot_ref=env_snapshot_ref,
            dispatch_argv=dispatch_argv,
            retries_attempted=retries_attempted,
            max_retries=max_retries,
            next_attempt_earliest_ts=cooling_until_ts or now_ts,
        )


def checkpoint_path_for(runs_dir: Path, run_id: str, process_slug: str) -> Path:
    """Canonical path for a retry_later checkpoint.

    ``<runs_dir>/<run_id>/<process_slug>/.state/retry_later.yaml``.
    We write YAML for operator readability; loading uses the same
    schema.
    """
    return runs_dir / run_id / process_slug / ".state" / "retry_later.yaml"


def write_checkpoint(
    checkpoint: RetryLaterCheckpoint,
    *,
    path: Path,
    env_snapshot: dict[str, str] | None = None,
) -> None:
    """Write *checkpoint* to *path* atomically; emit the env snapshot.

    Uses json-in-yaml format (YAML is a strict superset of JSON, so
    operators with ``yq`` / ``kubectl`` toolchains read fine) to
    avoid pulling in a YAML dependency here. The resume daemon
    uses the same reader.
    """

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = asdict(checkpoint)
    with atomic_output_file(path) as tmp:
        Path(tmp).write_text(json.dumps(data, indent=2, sort_keys=False))
        Path(tmp).chmod(0o600)

    if env_snapshot is not None and checkpoint.env_snapshot_ref:
        env_path = path.parent / checkpoint.env_snapshot_ref
        # Scrub obvious secrets from the env snapshot so a leaked
        # retry_later.env.json doesn't leak credentials.
        scrubbed = {k: v for k, v in env_snapshot.items() if not _looks_secret(k)}
        with atomic_output_file(env_path) as tmp:
            Path(tmp).write_text(json.dumps(scrubbed, indent=2))
            Path(tmp).chmod(0o600)


def _looks_secret(key: str) -> bool:
    """Return True for env var names that commonly hold secrets.

    Used only by the env-snapshot scrubber; the checkpoint itself
    never contains OAuth blobs by construction.
    """
    k = key.upper()
    return (
        "TOKEN" in k
        or "SECRET" in k
        or "API_KEY" in k
        or "OAUTH" in k
        or "PASSWORD" in k
        or "CREDS" in k
    )


def exit_78() -> int:
    """Surface the exit-78 constant for call sites.

    The dispatch command handles this specially so shell wrappers
    and the resume daemon see a consistent signal.
    """
    return RETRY_LATER_EXIT_CODE


def read_checkpoint(path: Path) -> RetryLaterCheckpoint:
    """Read + validate a checkpoint file.

    Raises ``FileNotFoundError`` when missing, ``ValueError`` on
    schema mismatch. Used by the resume daemon (P2c.5).
    """
    raw = path.read_text()
    data = json.loads(raw)
    if not isinstance(data, dict):
        msg = f"{path}: expected JSON object at top level"
        raise ValueError(msg)
    sv = data.get("schema_version", 0)
    if sv != 1:
        msg = f"{path}: unsupported checkpoint schema_version {sv!r}; this build expects 1"
        raise ValueError(msg)
    pool_snapshot = [PoolSnapshotEntry(**item) for item in data.get("pool_snapshot", [])]
    return RetryLaterCheckpoint(
        schema_version=sv,
        written_at_iso=data.get("written_at_iso", ""),
        written_at_ts=data.get("written_at_ts", 0),
        step_id=data.get("step_id", ""),
        cooling_until_ts=data.get("cooling_until_ts"),
        cooling_until_iso=data.get("cooling_until_iso", ""),
        from_step_arg=data.get("from_step_arg", ""),
        pool_snapshot=pool_snapshot,
        env_snapshot_ref=data.get("env_snapshot_ref", ""),
        dispatch_argv=list(data.get("dispatch_argv", [])),
        retries_attempted=data.get("retries_attempted", 0),
        max_retries=data.get("max_retries", 3),
        next_attempt_earliest_ts=data.get("next_attempt_earliest_ts", 0),
    )


__all__ = [
    "RETRY_LATER_EXIT_CODE",
    "PoolSnapshotEntry",
    "RetryLaterCheckpoint",
    "checkpoint_path_for",
    "exit_78",
    "read_checkpoint",
    "write_checkpoint",
]
