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
from metaproc.engine.resource_finalization import (
    finalize_run_resources,
    infer_recovery_outcome,
)
from metaproc.engine.resource_rollup import build_resource_artifacts
from metaproc.engine.resource_snapshot import read_resource_run_snapshot
from metaproc.engine.run_status import scan_run_status
from metaproc.errors import CLIError
from metaproc.models.resources import (
    MetricsV1,
    Node,
    NodeV1,
    ReadableResourcesDocument,
    ResourcesDocument,
    read_resources_document_json,
)
from metaproc.output import OutputFormat
from metaproc.viz_loader import load_plan_bundle, load_plan_bundle_from_run

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
            "Path to the .process.md that defines a legacy run without an immutable "
            "resource snapshot; ignored when reading a cached resources.json."
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
    rebuild it from evidence otherwise. ``--refresh`` forces a rebuild, using
    the immutable snapshot when available and ``--spec`` only for legacy runs.
    ``--json`` swaps the rendered tree for the persisted JSON contract
    so scripts can consume it directly.
    """
    out = get_output()
    if not run_dir.is_dir():
        raise CLIError(f"Run directory not found: {run_dir}")

    cached = run_dir / RESOURCES_FILENAME
    resolved_run_id = run_id or run_dir.name

    if refresh or not cached.exists():
        try:
            snapshot = read_resource_run_snapshot(run_dir)
        except Exception as exc:  # noqa: BLE001 - normalize persisted-contract errors for CLI
            raise CLIError(f"Invalid immutable resource snapshot under {run_dir}: {exc}") from exc
        if snapshot is not None:
            status = scan_run_status(run_dir, include_system=False)
            if status.is_active:
                raise CLIError("Resource recovery is unavailable while the run is active.")
            document = finalize_run_resources(
                run_dir,
                outcome=infer_recovery_outcome(run_dir, totals=status.totals),
                trigger="recovery",
                bundle=load_plan_bundle_from_run(run_dir),
            ).document
        elif spec is None:
            raise CLIError(
                "--spec is required to build resources.json for a legacy run without "
                f"an immutable resource snapshot (no cached copy found at {cached})."
            )
        else:
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
    """Persist a live rollup or the complete projections for an inactive run."""
    bundle = load_plan_bundle(spec, params=params)
    status = scan_run_status(run_dir, include_system=False)
    if not status.is_active:
        return finalize_run_resources(
            run_dir,
            outcome=infer_recovery_outcome(run_dir, totals=status.totals),
            legacy_run_id=run_id,
            trigger="recovery",
            bundle=bundle,
        ).document
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


def _read_cached(cache_path: Path) -> ReadableResourcesDocument:
    try:
        raw = cache_path.read_text()
    except OSError as exc:
        raise CLIError(f"Failed to read {cache_path}: {exc}") from exc
    try:
        return read_resources_document_json(raw)
    except Exception as exc:  # noqa: BLE001
        raise CLIError(
            f"Cached resources.json is malformed at {cache_path} ({exc!s}); "
            "rerun with --refresh to rebuild."
        ) from exc


def _render_tree(document: ReadableResourcesDocument) -> str:
    lines: list[str] = [
        f"resource-report run={document.run_id} schema={document.schema_version}",
        f"generated_at={document.generated_at.isoformat()}",
        "",
    ]
    _render_node(lines, document.hierarchy_root, indent=0)

    if isinstance(document, ResourcesDocument):
        totals = document.hierarchy_root.total_metrics
        lines.extend(
            [
                "",
                "costs:",
                f"  actual_usd: {_format_optional_number(totals.actual_cost_usd)}",
                f"  list_estimate_usd: {_format_optional_number(totals.list_cost_usd)}",
            ]
        )
        if document.finalization is not None:
            lines.extend(
                [
                    "",
                    "finalization:",
                    f"  outcome: {document.finalization.state.value}",
                    f"  trigger: {document.finalization.trigger}",
                    f"  recovered: {str(document.finalization.recovered).lower()}",
                ]
            )
        if document.meter_rollups:
            lines.extend(["", "provider_meters:"])
            for meter in document.meter_rollups:
                quantity = None
                if meter.coverage.value != "unmeasured":
                    quantity = (meter.actual_quantity or 0) + (meter.estimated_quantity or 0)
                value = "unmeasured" if quantity is None else f"{quantity:g}"
                lines.append(
                    f"  {'/'.join(meter.key.sort_key())}: {value} ({meter.coverage.value})"
                )
        if document.coverage_gaps:
            lines.extend(["", "coverage_gaps:"])
            lines.extend(f"  - {'/'.join(key.sort_key())}" for key in document.coverage_gaps)
        if document.budget_evaluations:
            lines.extend(["", "budgets:"])
            for evaluation in document.budget_evaluations:
                lines.append(
                    f"  {evaluation.budget.budget_id}: {evaluation.status.value} — "
                    f"{evaluation.message}"
                )

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


def _render_node(lines: list[str], node: Node | NodeV1, *, indent: int) -> None:
    pad = "  " * indent
    summary = _summary_value(node.total_metrics)
    lines.append(f"{pad}- [{node.node_type}] {node.label} ({node.node_id}) — {summary}")
    for child in node.children:
        _render_node(lines, child, indent=indent + 1)


def _summary_value(metrics: MetricsV1) -> str:
    parts: list[str] = []
    if metrics.wall_time_s is not None:
        parts.append(f"wall={metrics.wall_time_s:.2f}s")
    if metrics.actual_cost_usd is not None:
        parts.append(f"actual=${metrics.actual_cost_usd:.4f}")
    if metrics.list_cost_usd is not None:
        parts.append(f"list_est=${metrics.list_cost_usd:.4f}")
    if metrics.input_tokens is not None:
        parts.append(f"in_tok={metrics.input_tokens}")
    if metrics.output_tokens is not None:
        parts.append(f"out_tok={metrics.output_tokens}")
    if metrics.tool_calls is not None:
        parts.append(f"tool_calls={metrics.tool_calls}")
    if metrics.wait_throttling_s is not None and metrics.wait_throttling_s > 0:
        parts.append(f"throttle={metrics.wait_throttling_s:.2f}s")
    return ", ".join(parts) if parts else "—"


def _format_optional_number(value: float | None) -> str:
    return "unmeasured" if value is None else f"{value:.6f}"


def _ensure_imports_for_pyright() -> None:
    """No-op reference so pyright sees these imports as used in argparse types."""
    _ = json.dumps
