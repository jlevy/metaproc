"""metaproc kill — terminate a running pool or local orchestrator."""

from __future__ import annotations

import json
import os
import platform
import signal as signal_mod
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import typer

from metaproc.cli import app, get_output
from metaproc.io import read_yaml_file
from metaproc.io.orchestrator_lease import clear_orchestrator_lease
from metaproc.output import OutputFormat
from metaproc.paths import ORCHESTRATOR_LEASE_FILE, STATE_DIR
from metaproc.runpool.kill import KillResult, kill_pool


def _format_text(result: KillResult) -> str:
    """Format KillResult as a human-readable summary."""
    lines: list[str] = []

    lines.append(f"Pool: {result.pool_id}")
    lines.append(f"Kill requested at {result.requested_at:%Y-%m-%d %H:%M:%S}")
    lines.append("")

    killed_count = len(result.killed_processes)
    if result.drain:
        lines.append(f"  Drain mode: {result.drained_pending} pending items will not be launched")
        lines.append("  Running processes left to finish naturally.")
    else:
        lines.append(f"  Killed: {killed_count} processes ({result.signal_sent})")
        if result.drained_pending > 0:
            lines.append(
                f"  Drained: {result.drained_pending} pending items (will not be launched)"
            )

    if result.killed_processes:
        lines.append("")
        for kp in result.killed_processes:
            lines.append(f"  PID {kp.pid:<7} {kp.label:<50} {kp.status}")

    lines.append("")
    if result.pool_alive_after:
        lines.append("Pool still running (drain in progress or waiting for cleanup).")
    else:
        lines.append("Pool shutdown confirmed.")

    return "\n".join(lines)


def _format_json(result: KillResult) -> dict[str, object]:
    """Serialize KillResult to a JSON-compatible dict."""
    d = asdict(result)
    d["requested_at"] = result.requested_at.isoformat()
    d["scope"] = "pool"
    return d


def _kill_local_orchestrator(run_dir: Path, *, sig: str, force: bool) -> dict[str, object] | None:
    """Terminate a local orchestrator when no runpool has initialized yet.

    Early code steps and bundle stages can be running under only the
    orchestrator lease. In that window there is no ``runpool-status.yaml`` for
    :func:`kill_pool` to target, but the lease still gives us a same-host owner
    PID.
    """
    lease_path = run_dir / STATE_DIR / ORCHESTRATOR_LEASE_FILE
    if not lease_path.exists():
        return None
    raw = read_yaml_file(lease_path)
    if not isinstance(raw, dict):
        return None

    owner_host = str(raw.get("owner_host") or "")
    owner_pid = _parse_pid(raw.get("owner_pid"))
    owner_token = str(raw.get("owner_token") or "")
    if owner_host != platform.node():
        return {
            "scope": "orchestrator",
            "status": "remote_orchestrator",
            "owner_host": owner_host,
            "owner_pid": owner_pid,
            "signal_sent": "SIGKILL" if force else sig,
            "lease_cleared": False,
            "message": (
                "No local runpool exists, and the orchestrator lease belongs to a different host."
            ),
        }
    if owner_pid is None:
        return {
            "scope": "orchestrator",
            "status": "missing_owner_pid",
            "owner_host": owner_host,
            "owner_pid": None,
            "signal_sent": "SIGKILL" if force else sig,
            "lease_cleared": False,
            "message": "No local runpool exists, and the orchestrator lease has no PID.",
        }

    effective_sig = "SIGKILL" if force else sig
    os_sig = signal_mod.SIGKILL if force else _parse_signal(effective_sig)
    status = _send_signal(owner_pid, os_sig)
    if status == "signaled":
        status = _wait_for_exit(owner_pid)
    lease_cleared = status in {"terminated", "already_exited"} and clear_orchestrator_lease(
        run_dir, owner_token=owner_token or None
    )

    return {
        "scope": "orchestrator",
        "status": status,
        "owner_host": owner_host,
        "owner_pid": owner_pid,
        "signal_sent": effective_sig,
        "lease_cleared": lease_cleared,
        "message": "No runpool exists; targeted the local orchestrator lease owner.",
    }


def _parse_pid(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _parse_signal(value: str) -> signal_mod.Signals:
    try:
        return signal_mod.Signals[value]
    except KeyError:
        raise ValueError("Signal must be SIGTERM or SIGKILL") from None


def _send_signal(pid: int, sig: signal_mod.Signals) -> str:
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return "already_exited"
    except PermissionError:
        return "permission_denied"
    return "signaled"


def _wait_for_exit(pid: int) -> str:
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return "terminated"
        except PermissionError:
            return "terminated"
        time.sleep(0.2)
    return "still_running"


def _format_orchestrator_text(result: dict[str, object]) -> str:
    lines = [
        "No runpool status file found; targeted the orchestrator lease instead.",
        f"Orchestrator: {result.get('owner_host')} PID {result.get('owner_pid')}",
        f"Signal: {result.get('signal_sent')}",
        f"Status: {result.get('status')}",
        f"Lease cleared: {result.get('lease_cleared')}",
    ]
    message = result.get("message")
    if message:
        lines.append(str(message))
    return "\n".join(lines)


@app.command()
def kill(
    run_dir: Path = typer.Argument(..., help="Path to the run directory"),
    variant: str | None = typer.Option(
        None, "--variant", help="Kill only processes for a specific variant"
    ),  # noqa: UP007
    signal: str = typer.Option(
        "SIGTERM", "--signal", help="Signal to send: SIGTERM (default) or SIGKILL"
    ),
    drain: bool = typer.Option(
        False, "--drain", help="Don't kill running processes, just prevent new launches"
    ),
    force: bool = typer.Option(
        False, "--force", help="SIGKILL immediately, skip graceful SIGTERM phase"
    ),
    format: str | None = typer.Option(  # noqa: UP007
        None, "--format", help="Output format: text or json"
    ),
) -> None:
    """Terminate a running pool, or the local orchestrator before pool startup."""
    out = get_output()
    use_json = (format == "json") or (out.format == OutputFormat.JSON)

    try:
        result = kill_pool(run_dir, variant=variant, sig=signal, drain=drain, force=force)
    except FileNotFoundError as pool_error:
        error: Exception = pool_error
        try:
            orchestrator_result = _kill_local_orchestrator(run_dir, sig=signal, force=force)
        except ValueError as signal_error:
            orchestrator_result = None
            error = signal_error
        if orchestrator_result is not None:
            if use_json:
                json.dump(orchestrator_result, sys.stdout, default=str, indent=2)
                sys.stdout.write("\n")
            else:
                out.data(_format_orchestrator_text(orchestrator_result))
            if orchestrator_result["status"] in {
                "permission_denied",
                "remote_orchestrator",
                "missing_owner_pid",
                "still_running",
            }:
                raise SystemExit(1) from None
            return
        if use_json:
            json.dump({"error": str(error)}, sys.stdout)
            sys.stdout.write("\n")
        else:
            out.error(str(error))
        raise SystemExit(1) from error
    except RuntimeError as e:
        if use_json:
            json.dump({"error": str(e)}, sys.stdout)
            sys.stdout.write("\n")
        else:
            out.error(str(e))
        raise SystemExit(1) from e

    if use_json:
        json.dump(_format_json(result), sys.stdout, default=str, indent=2)
        sys.stdout.write("\n")
    else:
        out.data(_format_text(result))
