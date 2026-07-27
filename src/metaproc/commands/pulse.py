"""``metaproc pulse <run-dir>`` — one-line health pulse for a long run.

Replaces the deleted ``scripts/dispatch-pulse.sh`` (a 167-line bash that
hand-parsed lease YAML, ISO timestamps, and ``runpool-events.jsonl``). The PULSE
line is a stable one-liner the supervising agent emits each turn; it is a
*formatter* over metaproc's own run-dir state (orchestrator lease, pool status,
process/pool events), not new data collection.

PULSE line shape (operator-stable — do not edit mid-run):

    PULSE: orch=<alive|dead> pid=<pid|?> N=<done>/<total> hb=<age>s ev_age=<age>s \
           queue=<in-flight> recent_halts=<n>[ conc=<cur>/<max>][ ceil=<mem-ceil>] \
           [ pressure=<level>[@<pct>%]][ auth=<label:count|...>]

The ``conc``/``ceil``/``pressure`` fields are populated when ``runpool-status.yaml``
includes the matching ``current_concurrency`` / ``controller.memory_ceiling`` /
``pressure`` blocks. They are the canary the supervising agent uses to spot
the classic conc=1 clamp regression without grepping ``health.jsonl``
(operator pushback during the 2026-05-25 Wave 2 batch).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import psutil
import typer

from metaproc import paths as paths_mod
from metaproc.cli import app
from metaproc.io import read_yaml_file

# Pool-event names that signal a halt/failure worth surfacing in recent_halts.
_HALT_EVENTS = frozenset({"step_fail", "item_fail", "process_failed", "host_suspended"})


@dataclass
class PulseMetrics:
    """The metrics rendered into the PULSE line."""

    orch_state: str
    orch_pid: int | None
    done: int | None
    total: int | None
    hb_age_s: int | None
    ev_age_s: int | None
    queue: int
    recent_halts: int
    auth_dist: dict[str, int] = field(default_factory=dict)
    # Concurrency and pressure fields provide operator observability.
    # All ``None`` when ``runpool-status.yaml`` is missing the corresponding
    # field, in which case the segment is omitted from the rendered line.
    current_concurrency: int | None = None
    max_concurrency: int | None = None
    memory_ceiling: int | None = None
    pressure_level: str | None = None
    pressure_available_pct: float | None = None


def _q(value: int | None) -> str:
    return "?" if value is None else str(value)


def format_pulse(metrics: PulseMetrics) -> str:
    """Render the stable one-line PULSE string."""
    auth_seg = ""
    if metrics.auth_dist:
        auth_seg = " auth=" + "|".join(f"{k}:{v}" for k, v in metrics.auth_dist.items())
    conc_seg = ""
    if metrics.current_concurrency is not None or metrics.max_concurrency is not None:
        conc_seg = f" conc={_q(metrics.current_concurrency)}/{_q(metrics.max_concurrency)}"
    ceil_seg = ""
    if metrics.memory_ceiling is not None:
        ceil_seg = f" ceil={metrics.memory_ceiling}"
    pressure_seg = ""
    if metrics.pressure_level is not None:
        if metrics.pressure_available_pct is not None:
            pressure_seg = (
                f" pressure={metrics.pressure_level}@{metrics.pressure_available_pct:.0f}%"
            )
        else:
            pressure_seg = f" pressure={metrics.pressure_level}"
    return (
        f"PULSE: orch={metrics.orch_state} pid={_q(metrics.orch_pid)} "
        f"N={_q(metrics.done)}/{_q(metrics.total)} hb={_q(metrics.hb_age_s)}s "
        f"ev_age={_q(metrics.ev_age_s)}s queue={metrics.queue} "
        f"recent_halts={metrics.recent_halts}{conc_seg}{ceil_seg}{pressure_seg}{auth_seg}"
    )


def _file_age_s(path: Path) -> int | None:
    if not path.exists():
        return None
    return int(datetime.now(UTC).timestamp() - path.stat().st_mtime)


def _iso_age_s(ts: object) -> int | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(datetime.now(UTC).timestamp() - dt.timestamp())


def gather_pulse(run_dir: Path) -> PulseMetrics:
    """Read the run dir's authoritative state into :class:`PulseMetrics`.

    Defensive about missing files (a run that hasn't reached a given stage):
    unknown numeric fields are ``None`` and render as ``?``. Pool status and
    events are discovered under the tree so composite child pools are included.
    """
    state = run_dir / paths_mod.STATE_DIR

    orch_pid: int | None = None
    hb_age_s: int | None = None
    orch_state = "dead"
    lease = state / paths_mod.ORCHESTRATOR_LEASE_FILE
    if lease.exists():
        data = read_yaml_file(lease) or {}
        raw_pid = data.get("owner_pid")
        orch_pid = raw_pid if isinstance(raw_pid, int) else None
        hb_age_s = _iso_age_s(data.get("last_heartbeat_at"))
        if orch_pid is not None and psutil.pid_exists(orch_pid):
            orch_state = "alive"

    done: int | None = None
    total: int | None = None
    queue = 0
    current_concurrency: int | None = None
    max_concurrency: int | None = None
    memory_ceiling: int | None = None
    pressure_level: str | None = None
    pressure_available_pct: float | None = None
    primary_status = state / paths_mod.POOL_STATUS_FILE
    status_files = (
        [primary_status]
        if primary_status.exists()
        else sorted(run_dir.glob(f"**/{paths_mod.POOL_STATUS_FILE}"))
    )
    if status_files:
        # Pick the most recently updated status file when multiple steps have
        # one — the latest is the one the operator most likely cares about.
        # Sorted glob isn't time-ordered, so re-sort by mtime descending.
        if len(status_files) > 1:
            status_files = sorted(status_files, key=lambda p: p.stat().st_mtime, reverse=True)
        s = read_yaml_file(status_files[0]) or {}
        done = s.get("completed_count")
        queue = int(s.get("active_count") or 0)
        count_sum = sum(
            int(s.get(k) or 0)
            for k in (
                "completed_count",
                "active_count",
                "pending_count",
                "failed_count",
                "killed_count",
            )
        )
        total = count_sum or None
        raw_cur_conc = s.get("current_concurrency")
        if isinstance(raw_cur_conc, int):
            current_concurrency = raw_cur_conc
        raw_max_conc = s.get("max_concurrency")
        if isinstance(raw_max_conc, int):
            max_concurrency = raw_max_conc
        controller = s.get("controller") or {}
        raw_ceil = controller.get("memory_ceiling") if isinstance(controller, dict) else None
        if isinstance(raw_ceil, int):
            memory_ceiling = raw_ceil
        pressure = s.get("pressure") or {}
        if isinstance(pressure, dict):
            raw_level = pressure.get("level")
            if isinstance(raw_level, str):
                pressure_level = raw_level
            raw_pct = pressure.get("available_pct")
            if isinstance(raw_pct, int | float):
                pressure_available_pct = float(raw_pct)

    ev_age_s = _file_age_s(paths_mod.process_events_log(run_dir))

    auth_dist: dict[str, int] = {}
    recent_halts = 0
    for ev_file in run_dir.glob(f"**/{paths_mod.POOL_EVENTS_FILE}"):
        for line in ev_file.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            event = obj.get("event")
            if event == "auth_lease_acquired":
                label = obj.get("label")
                if label:
                    auth_dist[label] = auth_dist.get(label, 0) + 1
            elif event in _HALT_EVENTS:
                recent_halts += 1

    return PulseMetrics(
        orch_state=orch_state,
        orch_pid=orch_pid,
        done=done,
        total=total,
        hb_age_s=hb_age_s,
        ev_age_s=ev_age_s,
        queue=queue,
        recent_halts=recent_halts,
        auth_dist=auth_dist,
        current_concurrency=current_concurrency,
        max_concurrency=max_concurrency,
        memory_ceiling=memory_ceiling,
        pressure_level=pressure_level,
        pressure_available_pct=pressure_available_pct,
    )


@app.command("pulse")
def pulse(
    run_dir: Path = typer.Argument(
        ...,
        help=(
            "Run directory to read state from. With --batch, treat as a glob"
            " or parent dir matching multiple sibling-run dirs."
        ),
    ),
    batch: bool = typer.Option(
        False,
        "--batch",
        help=(
            "Treat run_dir as a glob (or parent dir) matching multiple sibling-run"
            " directories. Prints one PULSE line per match, prefixed with the lane"
            " name (last path segment of the run dir). For a 9-lane batch the"
            " operator gets the full status in one command instead of 9× pulse."
        ),
    ),
) -> None:
    """Print a one-line health pulse for a run (orchestrator, progress, auth)."""
    if batch:
        matches = _resolve_batch_run_dirs(run_dir)
        if not matches:
            typer.echo(f"pulse --batch: no run dirs match: {run_dir}", err=True)
            raise typer.Exit(2)
        lane_w = max(len(p.name) for p in matches)
        for lane_dir in matches:
            line = format_pulse(gather_pulse(lane_dir))
            typer.echo(f"{lane_dir.name:<{lane_w}}  {line}")
        return
    if not run_dir.exists():
        typer.echo(f"pulse: run dir not found: {run_dir}", err=True)
        raise typer.Exit(2)
    typer.echo(format_pulse(gather_pulse(run_dir)))


def _resolve_batch_run_dirs(spec: Path) -> list[Path]:
    """Resolve a --batch arg to a list of sibling-run dirs.

    Accepts three shapes (in order of detection):
    1. Glob pattern (str contains ``*``/``?``/``[``) — globs from cwd.
    2. Existing directory — returns its immediate subdirs that contain a
       ``.state`` dir (sibling-run dirs under a shared parent).
    3. Existing path — returns ``[spec]`` (operator passed a single run dir
       via --batch by mistake; treat as a one-lane batch rather than error).

    The result is sorted alphabetically so output ordering is stable across
    invocations — the supervising agent diffs successive pulses, so stable
    ordering matters.
    """
    spec_str = str(spec)
    if any(ch in spec_str for ch in "*?["):
        from glob import glob  # noqa: PLC0415 -- pre-existing local import; needs review

        return sorted(Path(p) for p in glob(spec_str))
    if spec.is_dir():
        children = [p for p in spec.iterdir() if p.is_dir() and (p / ".state").is_dir()]
        if children:
            return sorted(children)
        return [spec]
    return [spec] if spec.exists() else []
