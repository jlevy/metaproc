"""Code-mode handlers for the nested agent fan-out fixture."""

from __future__ import annotations

from pathlib import Path

from metaproc.models.authored import ProcessStep


def _run_dir(variables: dict[str, str]) -> Path:
    return Path(variables["RUNS_DIR"]) / variables["RUN_ID"]


def scaffold_roster(variables: dict[str, str], step: ProcessStep) -> None:  # noqa: ARG001
    """Write the roster the parent's composite step maps over."""
    path = _run_dir(variables) / "roster.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    units = [t.strip() for t in variables.get("UNITS", "AAA").split(",") if t.strip()]
    lines = [
        "---",
        "progress:",
        "  schema: metaproc:ProgressSpec/0.1",
        "  process: nested-roster",
        "  items:",
    ]
    lines += [f"  - unit: {unit}" for unit in units]
    lines += ["---", "# Roster", ""]
    path.write_text("\n".join(lines))


def scaffold_work(variables: dict[str, str], step: ProcessStep) -> None:  # noqa: ARG001
    """Write the per-item work list the child's agent step fans out over."""
    path = _run_dir(variables) / "work.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    unit = variables.get("UNIT", "AAA")
    body = [
        "---",
        "progress:",
        "  schema: metaproc:ProgressSpec/0.1",
        "  process: nested-work",
        "  items:",
        f"  - profile_task: {unit}",
        "---",
        "# Work",
        "",
    ]
    path.write_text("\n".join(body))
