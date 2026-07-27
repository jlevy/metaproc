"""metaproc compact-logs — strip redundant streaming events from JSONL logs."""

from __future__ import annotations

from pathlib import Path

import typer
from prettyfmt import fmt_size_human

from metaproc.cli import app, get_output
from metaproc.logutil.compaction import (
    CompactionResult,
    compact_log,
    compact_logs_path,
    plan_compaction,
)


def _parse_adapters(value: str) -> frozenset[str] | None:
    adapters = frozenset(part.strip() for part in value.split(",") if part.strip())
    return adapters or None


@app.command("compact-logs")
def compact_logs(
    path: Path = typer.Argument(..., help="File or directory to compact (.jsonl files)"),
    keep_original: bool = typer.Option(
        False, "--keep-original", help="Keep original as .jsonl.bak"
    ),
    min_size: int = typer.Option(
        0,
        "--min-size",
        help="Only compact files at least this many bytes. Default: 0.",
    ),
    adapters: str = typer.Option(
        "",
        "--adapters",
        help=(
            "Comma-separated adapter filter such as pi,codex. "
            "When omitted, preserves legacy behavior and considers every .jsonl file."
        ),
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be compacted"),
) -> None:
    """Compact JSONL log files by removing redundant streaming events.

    Walks the given path (file or directory) and compacts all .jsonl files.
    Safe to run multiple times — already-compacted files are skipped.
    """
    out = get_output()

    if path.is_file():
        files = [path]
    elif path.is_dir():
        files = sorted(path.rglob("*.jsonl"))
    else:
        out.data(f"Path not found: {path}")
        raise typer.Exit(code=1)

    if not files:
        out.data("No .jsonl files found.")
        return

    adapter_filter = _parse_adapters(adapters)

    total_saved = 0
    total_original = 0
    results: list[CompactionResult] = []

    if dry_run:
        plan = plan_compaction(path, min_size=min_size, adapters=adapter_filter)
        for candidate in plan.candidates:
            adapter = f", adapter={candidate.adapter}" if candidate.adapter else ""
            out.data(
                f"  would compact: {candidate.path} ({fmt_size_human(candidate.size)}{adapter})"
            )
        out.data(f"\nDry run: {plan.eligible} eligible, {plan.skipped} skipped")
        return

    if adapter_filter is not None or min_size:
        summary = compact_logs_path(
            path,
            keep_original=keep_original,
            min_size=min_size,
            adapters=adapter_filter,
        )
        out.data(
            f"\nDone: {summary.compacted} files compacted, "
            f"{summary.skipped} skipped, {fmt_size_human(summary.saved_bytes)} saved"
        )
        if summary.failed:
            raise typer.Exit(code=1)
        return

    for f in files:
        result = compact_log(f, keep_original=keep_original)
        results.append(result)
        total_original += result.original_size
        total_saved += result.bytes_saved

        if result.already_compact:
            out.data(f"  skip (already compact): {f}")
        elif result.lines_removed <= 0:
            out.data(f"  skip (no-op, {result.adapter}): {f}")
        else:
            out.data(
                f"  compacted ({result.adapter}): {f}"
                f"  {fmt_size_human(result.original_size)} -> {fmt_size_human(result.compacted_size)}"
                f"  ({result.reduction_pct:.0f}% reduction, {result.lines_removed:,d} lines removed)"
            )

    if not dry_run:
        compacted_count = sum(1 for r in results if not r.already_compact and r.lines_removed > 0)
        skipped_count = len(results) - compacted_count
        out.data(
            f"\nDone: {compacted_count} files compacted, {skipped_count} skipped,"
            f" {fmt_size_human(total_saved)} saved"
        )
