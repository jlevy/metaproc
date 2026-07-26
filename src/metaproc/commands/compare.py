"""metaproc compare — side-by-side comparison of two item directories."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import typer
from prettyfmt import fmt_timedelta

from metaproc.cli import app, get_output
from metaproc.commands.helpers import fmt_value
from metaproc.errors import ValidationError
from metaproc.io import read_yaml_file
from metaproc.paths import ATTEMPT_FILE, RESULT_FILE, STATE_DIR, STATUS_FILE


def _read_state_file(item_dir: Path, filename: str) -> dict[str, Any] | None:
    """Read a YAML state file, returning None if missing."""
    path = item_dir / STATE_DIR / filename
    if not path.exists():
        return None
    return read_yaml_file(path)


@app.command()
def compare(
    dir_a: Path = typer.Argument(..., help="First item directory"),
    dir_b: Path = typer.Argument(..., help="Second item directory"),
) -> None:
    """Compare results from two adapter runs side-by-side."""
    out = get_output()

    for label, d in [("dir_a", dir_a), ("dir_b", dir_b)]:
        if not d.exists():
            raise ValidationError(f"{label} not found: {d}")
        if not (d / STATE_DIR).exists():
            raise ValidationError(f"{label} has no {STATE_DIR}/ directory: {d}")

    attempt_a = _read_state_file(dir_a, ATTEMPT_FILE)
    attempt_b = _read_state_file(dir_b, ATTEMPT_FILE)
    status_a = _read_state_file(dir_a, STATUS_FILE)
    status_b = _read_state_file(dir_b, STATUS_FILE)
    result_a = _read_state_file(dir_a, RESULT_FILE)
    result_b = _read_state_file(dir_b, RESULT_FILE)

    runtime_a = (attempt_a or {}).get("runtime", {})
    runtime_b = (attempt_b or {}).get("runtime", {})

    rows: list[tuple[str, str, str]] = [
        (
            "adapter",
            fmt_value(runtime_a.get("adapter_type")),
            fmt_value(runtime_b.get("adapter_type")),
        ),
        ("model", fmt_value(runtime_a.get("model")), fmt_value(runtime_b.get("model"))),
        (
            "status",
            fmt_value((status_a or {}).get("state")),
            fmt_value((status_b or {}).get("state")),
        ),
    ]

    def _duration(status: dict[str, Any] | None) -> str:
        if not status or not status.get("started_at") or not status.get("completed_at"):
            return "N/A"
        try:
            start = datetime.fromisoformat(status["started_at"])
            end = datetime.fromisoformat(status["completed_at"])
            return fmt_timedelta((end - start).total_seconds())
        except (ValueError, TypeError):
            return "N/A"

    rows.append(("duration", _duration(status_a), _duration(status_b)))
    rows.append(
        (
            "validated",
            fmt_value((result_a or {}).get("validated")),
            fmt_value((result_b or {}).get("validated")),
        )
    )

    col_w = max((len(r[1]) for r in rows), default=10)
    col_w = max(col_w, max((len(r[2]) for r in rows), default=10), 10)

    out.data(f"{'Field':<20}  {'A':^{col_w}}  {'B':^{col_w}}")
    out.data(f"{'─' * 20}  {'─' * col_w}  {'─' * col_w}")
    for field, va, vb in rows:
        marker = " " if va == vb else "*"
        out.data(f"{field:<20}  {va:^{col_w}}  {vb:^{col_w}}  {marker}")

    # Output file comparison
    outputs_a = (result_a or {}).get("outputs", {})
    outputs_b = (result_b or {}).get("outputs", {})
    all_output_keys = sorted(set(outputs_a) | set(outputs_b))

    if all_output_keys:
        out.data("\nOutput files:")
        for key in all_output_keys:
            path_a = dir_a / Path(outputs_a.get(key, "")).name if outputs_a.get(key) else None
            path_b = dir_b / Path(outputs_b.get(key, "")).name if outputs_b.get(key) else None
            exists_a = path_a and path_a.exists()
            exists_b = path_b and path_b.exists()

            if exists_a and exists_b and path_a and path_b:
                content_a = path_a.read_text()
                content_b = path_b.read_text()
                if content_a == content_b:
                    out.data(f"  {key}: identical")
                else:
                    lines_a = content_a.splitlines()
                    lines_b = content_b.splitlines()
                    changed = sum(1 for a, b in zip(lines_a, lines_b, strict=False) if a != b)
                    out.data(f"  {key}: {changed} lines differ")
            elif exists_a:
                out.data(f"  {key}: A only")
            elif exists_b:
                out.data(f"  {key}: B only")
            else:
                out.data(f"  {key}: missing in both")

    out.data(f"\nA: {dir_a}")
    out.data(f"B: {dir_b}")
