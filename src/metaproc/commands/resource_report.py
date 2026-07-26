"""metaproc resource-report — emit a hierarchical resource roll-up for a run.

Persists / refreshes ``resources.json`` under the run dir and renders a
human-readable tree by default. Operators get the same data as the
browser's ``/api/resources`` endpoint without leaving the terminal.

Naming note: the existing ``metaproc resources`` command reports the
*calling process's* host/cgroup context — it has nothing to do with
this run-level rollup, so the command is ``metaproc resource-report``.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from strif import atomic_output_file

from metaproc.cli import app, get_output
from metaproc.engine.resource_rollup import build_resource_artifacts
from metaproc.errors import CLIError
from metaproc.models.resources import (
    Metrics,
    Node,
    ResourcesDocument,
)
from metaproc.output import OutputFormat
from metaproc.viz_loader import load_plan_bundle

RESOURCES_FILENAME = "resources.json"


@app.command("resource-report")
def resource_report(
    run_dir: Path = typer.Argument(
        ...,
        help="Run directory containing the run's logs and (after first build) resources.json.",
    ),
    spec: Path | None = typer.Option(
        None,
        "--spec",
        help=(
            "Path to the .process.md that defines this run. Required on first build "
            "and when --refresh is set; ignored when reading a cached resources.json."
        ),
    ),
    run_id: str = typer.Option(
        "",
        "--run-id",
        help="Run identifier (defaults to the run dir's basename).",
    ),
    refresh: bool = typer.Option(
        False,
        "--refresh",
        help="Rebuild resources.json from evidence even if a cached copy exists.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the resources.json contract as JSON instead of the text tree.",
    ),
    params: list[str] = typer.Option(
        [],
        "--param",
        help="Process input override in KEY=VALUE form (repeatable).",
    ),
) -> None:
    """Render the hierarchical resource roll-up for ``RUN_DIR``.

    Default behaviour: read the persisted ``resources.json`` if present;
    rebuild it from evidence otherwise. ``--refresh`` forces a rebuild.
    ``--json`` swaps the rendered tree for the persisted JSON contract
    so scripts can consume it directly.
    """
    out = get_output()
    if not run_dir.is_dir():
        raise CLIError(f"Run directory not found: {run_dir}")

    cached = run_dir / RESOURCES_FILENAME
    resolved_run_id = run_id or run_dir.name

    if refresh or not cached.exists():
        if spec is None:
            raise CLIError(
                f"--spec is required to build resources.json (no cached copy found at {cached})."
            )
        document = _build_and_persist(
            spec=spec,
            run_dir=run_dir,
            run_id=resolved_run_id,
            cache_path=cached,
            params=_parse_params(params),
        )
    else:
        document = _read_cached(cached)

    if json_output or out.format == OutputFormat.JSON:
        out.data(document.model_dump_json(by_alias=True, indent=2))
        return

    out.data(_render_tree(document))


def _parse_params(items: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise CLIError(f"--param must be KEY=VALUE, got {item!r}")
        key, _, value = item.partition("=")
        parsed[key.strip()] = value.strip()
    return parsed


def _build_and_persist(
    *,
    spec: Path,
    run_dir: Path,
    run_id: str,
    cache_path: Path,
    params: dict[str, str],
) -> ResourcesDocument:
    """Build the rollup, persist resources.json + resource-events.jsonl atomically."""
    bundle = load_plan_bundle(spec, params=params)
    result = build_resource_artifacts(
        bundle=bundle,
        run_dir=run_dir,
        run_id=run_id,
        document_path=cache_path,
        write=True,
    )
    return result.document


def _persist_document_atomic(document: ResourcesDocument, cache_path: Path) -> None:
    """Persist just the document atomically (no events file rewrite)."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_output_file(cache_path) as tmp:
        tmp.write_text(document.model_dump_json(by_alias=True, indent=2))


def _read_cached(cache_path: Path) -> ResourcesDocument:
    try:
        raw = cache_path.read_text()
    except OSError as exc:
        raise CLIError(f"Failed to read {cache_path}: {exc}") from exc
    try:
        return ResourcesDocument.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001
        raise CLIError(
            f"Cached resources.json is malformed at {cache_path} ({exc!s}); "
            "rerun with --refresh to rebuild."
        ) from exc


def _render_tree(document: ResourcesDocument) -> str:
    lines: list[str] = [
        f"resource-report run={document.run_id} schema={document.schema_version}",
        f"generated_at={document.generated_at.isoformat()}",
        "",
    ]
    _render_node(lines, document.hierarchy_root, indent=0)

    if document.taxonomy_rollups:
        lines.append("")
        lines.append("taxonomy_rollups:")
        for family, rollups in sorted(document.taxonomy_rollups.items()):
            lines.append(f"  {family}:")
            for rollup in rollups:
                value = _summary_value(rollup.metrics)
                lines.append(f"    {rollup.canonical}: {value}")

    if document.source_logs:
        lines.append("")
        lines.append("source_logs:")
        for sl in document.source_logs:
            lines.append(
                f"  [{sl.kind}] {sl.path} adapter={sl.adapter or '?'} "
                f"events={sl.summary.event_count} owner={sl.owner_node_id or '<unattributed>'}"
            )

    return "\n".join(lines)


def _render_node(lines: list[str], node: Node, *, indent: int) -> None:
    pad = "  " * indent
    summary = _summary_value(node.total_metrics)
    lines.append(f"{pad}- [{node.node_type}] {node.label} ({node.node_id}) — {summary}")
    for child in node.children:
        _render_node(lines, child, indent=indent + 1)


def _summary_value(metrics: Metrics) -> str:
    parts: list[str] = []
    if metrics.wall_time_s is not None:
        parts.append(f"wall={metrics.wall_time_s:.2f}s")
    if metrics.actual_cost_usd is not None:
        parts.append(f"cost=${metrics.actual_cost_usd:.4f}")
    if metrics.input_tokens is not None:
        parts.append(f"in_tok={metrics.input_tokens}")
    if metrics.output_tokens is not None:
        parts.append(f"out_tok={metrics.output_tokens}")
    if metrics.tool_calls is not None:
        parts.append(f"tool_calls={metrics.tool_calls}")
    if metrics.wait_throttling_s is not None and metrics.wait_throttling_s > 0:
        parts.append(f"throttle={metrics.wait_throttling_s:.2f}s")
    return ", ".join(parts) if parts else "—"


def _ensure_imports_for_pyright() -> None:
    """No-op reference so pyright sees these imports as used in argparse types."""
    _ = json.dumps
