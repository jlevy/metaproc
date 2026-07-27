"""metaproc liveness-watch — stall backstop for long-running dispatches.

A second supervisor that runs under Claude Code's `Monitor(persistent=true)`
or `Bash(run_in_background=true)` alongside the primary streaming log filter.
The primary catches what the dispatch *says*; this catches *whether the
dispatch is saying anything at all*.

Polls one or more freshness signals on a fixed cadence and emits state
transitions to stdout. Each emitted line is one event for the Monitor to
pick up. Hysteresis keeps output to one line per transition — on entering
stall, on recovering — instead of flooding on every poll.

Signals are fully generic: point `--mtime` at any file whose mtime advances
while work is progressing, or `--heartbeat` at any YAML file with an ISO-8601
timestamp field. No knowledge of domain-specific dispatches or runtimes.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from glob import glob
from pathlib import Path

import typer

from metaproc.cli import app
from metaproc.io import read_yaml_file


@dataclass
class Signal:
    label: str
    getter: Callable[[], float | None]
    stale_seconds: int


def _mtime_ts(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return None


def _yaml_heartbeat_ts(path: Path, field: str) -> float | None:
    try:
        data = read_yaml_file(path)
    except FileNotFoundError:
        return None
    if not isinstance(data, dict):
        return None
    value = data.get(field)
    if value is None:
        return None
    iso = str(value).rstrip("Z")
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def _split_threshold(spec: str, default_stale: int) -> tuple[str, int]:
    if "=" in spec:
        body, _, stale_str = spec.rpartition("=")
        return body, int(stale_str)
    return spec, default_stale


def _parse_mtime_arg(spec: str, default_stale: int) -> Signal:
    body, stale = _split_threshold(spec, default_stale)
    path = Path(body)
    return Signal(
        label=f"mtime:{path}",
        getter=lambda: _mtime_ts(path),
        stale_seconds=stale,
    )


def _parse_heartbeat_arg(spec: str, default_stale: int) -> Signal:
    body, stale = _split_threshold(spec, default_stale)
    path_str, _, field = body.rpartition(":")
    if not path_str or not field:
        raise typer.BadParameter(f"--heartbeat must be PATH:FIELD[=STALE_SECONDS], got {spec!r}")
    path = Path(path_str)
    return Signal(
        label=f"hb:{path}:{field}",
        getter=lambda: _yaml_heartbeat_ts(path, field),
        stale_seconds=stale,
    )


@dataclass
class GlobSpec:
    """A glob pattern that expands to zero-or-more concrete signals each poll.

    Used for auto-discovering new orchestrator-lease.yaml files (or other
    per-lane state files) as new lanes launch during a multi-tier batch.
    Re-evaluating the glob prevents newly launched lanes from going unmonitored.
    """

    label_prefix: str
    pattern: str
    field: str | None  # None = mtime glob; set = heartbeat glob
    stale_seconds: int

    def expand(self) -> list[Signal]:
        """Glob-expand the pattern at poll time. Returns one Signal per match."""

        matches = sorted(glob(self.pattern))
        signals: list[Signal] = []
        for match in matches:
            path = Path(match)
            if self.field is None:
                signals.append(
                    Signal(
                        label=f"{self.label_prefix}:{path}",
                        getter=lambda p=path: _mtime_ts(p),
                        stale_seconds=self.stale_seconds,
                    )
                )
            else:
                field = self.field
                signals.append(
                    Signal(
                        label=f"{self.label_prefix}:{path}:{field}",
                        getter=lambda p=path, f=field: _yaml_heartbeat_ts(p, f),
                        stale_seconds=self.stale_seconds,
                    )
                )
        return signals


def _parse_mtime_glob_arg(spec: str, default_stale: int) -> GlobSpec:
    body, stale = _split_threshold(spec, default_stale)
    return GlobSpec(
        label_prefix="mtime",
        pattern=body,
        field=None,
        stale_seconds=stale,
    )


def _parse_heartbeat_glob_arg(spec: str, default_stale: int) -> GlobSpec:
    body, stale = _split_threshold(spec, default_stale)
    path_pattern, _, field = body.rpartition(":")
    if not path_pattern or not field:
        raise typer.BadParameter(
            f"--heartbeat-glob must be GLOB:FIELD[=STALE_SECONDS], got {spec!r}"
        )
    return GlobSpec(
        label_prefix="hb",
        pattern=path_pattern,
        field=field,
        stale_seconds=stale,
    )


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _format_ages(ages: list[tuple[str, float | None, int]]) -> str:
    parts = []
    for lbl, age, limit in ages:
        if age is None:
            parts.append(f"{lbl}=missing/limit={limit}s")
        else:
            parts.append(f"{lbl}={age:.0f}s/limit={limit}s")
    return ", ".join(parts)


@app.command("liveness-watch")
def liveness_watch(
    mtime: list[str] = typer.Option(
        [],
        "--mtime",
        help="Watch a file's mtime. Repeatable. Format: PATH or PATH=STALE_SECONDS.",
    ),
    heartbeat: list[str] = typer.Option(
        [],
        "--heartbeat",
        help=(
            "Watch a YAML heartbeat field. Repeatable. "
            "Format: PATH:FIELD or PATH:FIELD=STALE_SECONDS."
        ),
    ),
    mtime_glob: list[str] = typer.Option(
        [],
        "--mtime-glob",
        help=(
            "Watch every file mtime matching a glob pattern. Re-evaluated each "
            "poll cycle so new files (e.g., new orchestrator-lease.yaml as "
            "lanes launch) are auto-discovered. Repeatable. Format: GLOB or "
            "GLOB=STALE_SECONDS."
        ),
    ),
    heartbeat_glob: list[str] = typer.Option(
        [],
        "--heartbeat-glob",
        help=(
            "Watch YAML heartbeat fields across every file matching a glob "
            "pattern. Re-evaluated each poll cycle for auto-discovery of new "
            "lane lease files. Repeatable. Format: GLOB:FIELD or "
            "GLOB:FIELD=STALE_SECONDS. Example: "
            "'<runs-dir>/run-*/.state/orchestrator-lease.yaml:last_heartbeat_at=300'."
        ),
    ),
    stale_seconds: int = typer.Option(
        600,
        "--stale-seconds",
        help="Default staleness threshold for signals without an explicit one.",
    ),
    poll_seconds: int = typer.Option(
        60,
        "--poll-seconds",
        help="Seconds between checks.",
    ),
    grace_seconds: int = typer.Option(
        60,
        "--grace-seconds",
        help=(
            "Suppress stall emissions for this many seconds after startup, so a"
            " dispatch that's still bootstrapping isn't reported as stalled."
        ),
    ),
    once: bool = typer.Option(
        False,
        "--once",
        help=(
            "One-shot mode: exit 1 on the first stall transition. Use with"
            " Bash(run_in_background=true) for a single completion notification."
        ),
    ),
) -> None:
    """Stall backstop: emit on stall/recovery transitions across freshness signals.

    Runs forever by default, printing one line per transition:

        STALL ENTERED <iso>  <signals…>
        HEALTH RESTORED <iso> <signals…>

    With --once the command exits with code 1 on the first stall, which turns a
    Bash(run_in_background=true) task into a re-armable single-shot detonator.

    Use --mtime-glob / --heartbeat-glob for batches that launch additional
    lanes over time. The glob is re-expanded each poll cycle, so new lane
    lease files (or other dynamic per-lane state) are auto-discovered without
    restarting the watcher.
    """
    static_signals: list[Signal] = [_parse_mtime_arg(s, stale_seconds) for s in mtime]
    static_signals += [_parse_heartbeat_arg(s, stale_seconds) for s in heartbeat]
    glob_specs: list[GlobSpec] = [_parse_mtime_glob_arg(s, stale_seconds) for s in mtime_glob]
    glob_specs += [_parse_heartbeat_glob_arg(s, stale_seconds) for s in heartbeat_glob]
    if not static_signals and not glob_specs:
        raise typer.BadParameter(
            "supply at least one --mtime, --heartbeat, --mtime-glob, or --heartbeat-glob signal"
        )

    started = time.time()
    stalled = False
    while True:
        now = time.time()
        # Re-expand globs each poll to pick up newly-launched lane state files
        # (and to drop signals for vanished files — clean-exit orchestrators
        # release their lease, which would otherwise look like a stale heartbeat).
        signals: list[Signal] = list(static_signals)
        for spec in glob_specs:
            signals.extend(spec.expand())

        ages: list[tuple[str, float | None, int]] = []
        any_stale = False
        for s in signals:
            ts = s.getter()
            age = None if ts is None else now - ts
            ages.append((s.label, age, s.stale_seconds))
            if age is None or age > s.stale_seconds:
                any_stale = True

        in_grace = (now - started) < grace_seconds

        if any_stale and not stalled and not in_grace:
            print(f"STALL ENTERED {_now_iso()} {_format_ages(ages)}", flush=True)
            if once:
                sys.exit(1)
            stalled = True
        elif not any_stale and stalled:
            print(f"HEALTH RESTORED {_now_iso()} {_format_ages(ages)}", flush=True)
            stalled = False

        time.sleep(poll_seconds)
