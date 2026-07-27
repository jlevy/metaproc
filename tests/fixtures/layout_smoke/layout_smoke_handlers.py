"""Code-mode handlers for the run-dir layout smoke fixture.

Pure-Python handlers, no external dependencies — used by the golden-layout
integration test to exercise both per-step and per-task state writes
without needing a real adapter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from metaproc.models.authored import ProcessStep


def scaffold_items(variables: dict[str, str], step: ProcessStep) -> None:  # noqa: ARG001
    """Write a tiny items.md with two synthetic items."""
    run_dir = Path(variables["RUNS_DIR"]) / variables["RUN_ID"]
    items_path = run_dir / "items.md"
    items_path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "---\n"
        "items:\n"
        "  date: '2026-05-10'\n"
        "  process: predict\n"
        "  items:\n"
        "    - item: AAA\n"
        "    - item: BBB\n"
        "---\n"
        "scaffold items\n"
    )
    items_path.write_text(body)


def write_artifact(variables: dict[str, str], step: ProcessStep) -> None:  # noqa: ARG001
    """Write one artifact file per task at the spec-templated path."""
    run_dir = Path(variables["RUNS_DIR"]) / variables["RUN_ID"]
    item = variables["item"]
    artifact = run_dir / "artifacts" / item / "out.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(f"---\nticker: {item}\n---\nartifact for {item}\n")


def summarize(variables: dict[str, str], step: ProcessStep) -> None:  # noqa: ARG001
    """Read each per-task artifact and concatenate into a run-level summary."""
    run_dir = Path(variables["RUNS_DIR"]) / variables["RUN_ID"]
    artifacts_dir = run_dir / "artifacts"
    summary_lines: list[str] = []
    if artifacts_dir.exists():
        for ticker_dir in sorted(artifacts_dir.iterdir()):
            if not ticker_dir.is_dir():
                continue
            out = ticker_dir / "out.md"
            if out.exists():
                summary_lines.append(f"{ticker_dir.name}: {out.read_text().strip()}")
    summary_path = run_dir / "summary.md"
    summary_path.write_text("---\nsummary: true\n---\n" + "\n".join(summary_lines) + "\n")


def _items_provider(variables: dict[str, str]) -> list[dict[str, Any]]:
    """Optional helper used by tests that need to load the items dynamically."""
    run_dir = Path(variables["RUNS_DIR"]) / variables["RUN_ID"]
    items_path = run_dir / "items.md"
    text = items_path.read_text()
    parts = text.split("---", 2)
    if len(parts) < 3:
        return []
    parsed = yaml.safe_load(parts[1]) or {}
    return list(parsed.get("items", []))
