"""Python sidekick for the Metaproc MetaBrowser plugin.

Owns metaproc-domain server-side handlers for plugin routes. Core metabrowser
depends only on the generic plugin protocol; this sidekick owns the handlers
that require metaproc internals.

Handlers exposed under ``/api/plugin/metaproc/<route>``:

* ``info`` — plugin metadata (name, version, kinds claimed). Cheap
  smoke-test endpoint.
* ``viz-model`` — process-spec VizModel envelope.
* ``charts`` — runpool-log and agent-log chart series. Agent-log charts use
  MetaBrowser's public chart API; runpool-log charts use Metaproc's
  ``stats.analysis`` for full event aggregation.
* ``stats`` — runpool-log run stats. Wraps
  ``metaproc.stats.engine.compute_run_stats``.
* ``resources`` — resource-report rollups. Wraps
  ``metaproc.engine.resource_rollup.build_resource_artifacts``.
* ``structure-report`` — read-only softschema structure projection for a
  process and optional run directory.

The handlers use MetaBrowser's public path resolvers so plugin requests cannot
escape the served root.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict, cast

from metabrowser import (
    ArtifactPath,
    detect_adapter,
    extract_agent_charts_cached,
    relativize_path,
    resolve_directory,
    resolve_path,
)
from metabrowser import (
    CLIError as MetabrowserCLIError,
)
from starlette.responses import JSONResponse

from metaproc.engine.write_boundary import WriteBoundaryOverlapError
from metaproc.errors import CLIError as MetaprocCLIError
from metaproc.io import read_yaml_file
from metaproc.models.plan_bundle import PlanBundle
from metaproc.paths import run_config_file
from metaproc.runtime_projection import scan_task_output_projection
from metaproc.stats.engine import compute_run_stats
from metaproc.viz import build_viz_model
from metaproc.viz_loader import load_plan_bundle, scan_bundle_progress

if TYPE_CHECKING:
    from starlette.requests import Request


class ValidationWarning(TypedDict):
    """Machine-readable warning returned with a usable partial view."""

    code: str
    message: str


# ── /info ───────────────────────────────────────────────────────


def info_handler(request: Request) -> JSONResponse:  # noqa: ARG001
    """Return plugin metadata + the kinds it claims."""
    return JSONResponse(
        {
            "name": "metaproc",
            "version": "0.0.1",
            "kinds": [
                "process-spec",
                "resource-report",
                "structure-report",
                "runpool-log",
                "process-log",
            ],
            "routes": [
                "info",
                "viz-model",
                "charts",
                "stats",
                "resources",
                "structure-report",
            ],
        }
    )


# ── /viz-model ──────────────────────────────────────────────────


def _relativize_io_paths(entries: Any) -> None:
    """Rewrite path-bearing fields in an input/output declaration mapping."""
    for entry in entries.values():
        if entry.path is not None:
            entry.path = relativize_path(entry.path)
        template = getattr(entry, "template", None)
        if template is not None:
            entry.template = relativize_path(template)


def _relativize_process_header_paths(header: Any) -> None:
    """Rewrite every path carried by a process header."""
    header.source_path = relativize_path(header.source_path) or header.source_path
    _relativize_io_paths(header.process_inputs)
    for entry in header.process_inputs.values():
        if entry.as_type == "path" and isinstance(entry.default, str):
            entry.default = relativize_path(entry.default)
    _relativize_io_paths(header.process_outputs)


def _relativize_viz_paths(viz: Any) -> None:
    """Rewrite every path field on a ``VizModel`` to be ROOT_DIR-relative."""
    viz.root_process = relativize_path(viz.root_process) or viz.root_process
    if viz.header is not None:
        _relativize_process_header_paths(viz.header)
    for node in viz.nodes:
        if node.path is not None:
            node.path = relativize_path(node.path)
        if node.step is not None:
            node.step.source_path = relativize_path(node.step.source_path) or node.step.source_path
            if node.step.uses_path is not None:
                node.step.uses_path = relativize_path(node.step.uses_path)
            if node.step.output_root is not None:
                node.step.output_root = relativize_path(node.step.output_root)
            node.step.prompt_paths = [relativize_path(p) or p for p in node.step.prompt_paths]
            _relativize_io_paths(node.step.inputs)
            _relativize_io_paths(node.step.outputs)
            if node.step.fan_out is not None and node.step.fan_out.source is not None:
                node.step.fan_out.source = (
                    relativize_path(node.step.fan_out.source) or node.step.fan_out.source
                )
        if node.dep is not None:
            node.dep.path = relativize_path(node.dep.path) or node.dep.path
            node.dep.source_path = relativize_path(node.dep.source_path) or node.dep.source_path
        if node.process is not None:
            _relativize_process_header_paths(node.process)
    if viz.progress is not None:
        viz.progress.run_dir = relativize_path(viz.progress.run_dir) or viz.progress.run_dir
    if viz.task_projection is not None:
        viz.task_projection.run_dir = (
            relativize_path(viz.task_projection.run_dir) or viz.task_projection.run_dir
        )
        for task in viz.task_projection.tasks:
            for output in (*task.accepted_outputs, *task.unaccepted_outputs):
                if output.path is not None:
                    output.path = relativize_path(output.path) or output.path


def _warning(code: str, exc: BaseException) -> ValidationWarning:
    return {"code": code, "message": str(exc)}


def _load_run_bundle(run_dir: Path) -> PlanBundle:
    """Load a run's recorded process without leaving the served browser root."""
    config_path = run_config_file(run_dir)
    try:
        config_is_contained = config_path.resolve().is_relative_to(run_dir.resolve())
    except OSError:
        config_is_contained = False
    if not config_path.is_file() or not config_is_contained:
        raise ValueError(f"{config_path}: run config not found")
    raw = read_yaml_file(config_path)
    if not isinstance(raw, Mapping):
        raise ValueError(f"{config_path}: run config must be a mapping")
    process_spec = raw.get("process_spec")
    if not isinstance(process_spec, str) or not process_spec:
        raise ValueError(f"{config_path}: process_spec must be a non-empty string")
    target = resolve_path(process_spec)
    if target is None or not target.is_file():
        raise ValueError(f"{config_path}: process_spec is not available under the served root")
    variables_raw = raw.get("variables", {})
    if not isinstance(variables_raw, Mapping) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in variables_raw.items()
    ):
        raise ValueError(f"{config_path}: variables must be a string-to-string mapping")
    variables = cast(dict[str, str], dict(variables_raw))
    return load_plan_bundle(target, params=variables, validate_spec=False)


def viz_model_handler(request: Request) -> JSONResponse:
    """Return a VizModel envelope for the given process path."""
    process = request.query_params.get("process", "")
    run_dir_param = request.query_params.get("run_dir")
    run_dir_path = resolve_directory(run_dir_param) if run_dir_param is not None else None
    warnings: list[ValidationWarning] = []

    if process:
        target = resolve_path(process)
        if target is None or not target.is_file():
            return JSONResponse({"error": "Not found", "process": process}, status_code=404)
        try:
            bundle = load_plan_bundle(target)
        except WriteBoundaryOverlapError as overlap_exc:
            return JSONResponse(
                {
                    "error": "Process spec has overlapping agent write boundaries",
                    "error_code": "write_boundary_overlap",
                    "process": process,
                    "overlaps": [
                        {
                            "left_step": overlap.left_step,
                            "right_step": overlap.right_step,
                            "left_path": str(overlap.left_path),
                            "right_path": str(overlap.right_path),
                        }
                        for overlap in overlap_exc.overlaps
                    ],
                    "suggested_fix": (
                        overlap_exc.overlaps[0].suggested_fix if overlap_exc.overlaps else ""
                    ),
                },
                status_code=422,
            )
        except (OSError, ValueError, MetabrowserCLIError, MetaprocCLIError) as exc:
            warnings.append(_warning("process_validation_failed", exc))
            try:
                bundle = load_plan_bundle(target, validate_spec=False)
            except (OSError, ValueError, MetabrowserCLIError, MetaprocCLIError) as exc2:
                return JSONResponse(
                    {"error": "Failed to load process", "process": process, "detail": str(exc2)},
                    status_code=400,
                )
    elif run_dir_path is not None:
        try:
            bundle = _load_run_bundle(run_dir_path)
        except (OSError, ValueError, MetabrowserCLIError, MetaprocCLIError) as exc:
            return JSONResponse(
                {
                    "error": "Failed to load run process",
                    "run_dir": run_dir_param,
                    "detail": str(exc),
                },
                status_code=400,
            )
    else:
        status_code = 400 if run_dir_param is not None else 404
        return JSONResponse(
            {"error": "Not found", "process": process, "run_dir": run_dir_param},
            status_code=status_code,
        )

    progress = None
    task_projection = None
    if run_dir_path is not None:
        try:
            progress = scan_bundle_progress(run_dir_path, bundle)
        except (OSError, ValueError, MetabrowserCLIError, MetaprocCLIError) as exc:
            warnings.append(_warning("runtime_progress_unavailable", exc))
        try:
            task_projection = scan_task_output_projection(run_dir_path, bundle)
        except (OSError, ValueError, MetabrowserCLIError, MetaprocCLIError) as exc:
            warnings.append(_warning("runtime_projection_unavailable", exc))
    elif run_dir_param is not None:
        warnings.append(
            {
                "code": "run_context_unavailable",
                "message": "run_dir not found or outside the served root",
            }
        )

    viz = build_viz_model(
        bundle,
        progress=progress,
        task_projection=task_projection,
    )
    _relativize_viz_paths(viz)
    payload: dict[str, Any] = {"viz": viz.model_dump(by_alias=True, exclude_none=True)}
    if warnings:
        payload["validation_warnings"] = warnings
    return JSONResponse(payload)


# ── /charts ─────────────────────────────────────────────────────


async def charts_handler(request: Request) -> JSONResponse:
    """Return chart data (summary tally + time-series) for a JSONL log file.

    Sniffs the adapter from the head of the file, dispatches to the
    matching extractor (runpool or agent), and returns a JSON payload of
    ``{summary, charts}``. The extractors do their own one-pass adapter
    detection internally.
    """

    subpath = request.query_params.get("path", "")
    target = resolve_path(subpath)
    if target is None or not target.is_file():
        return JSONResponse({"error": "Not found"}, status_code=404)

    artifact = ArtifactPath(target)
    ext = artifact.logical_ext
    if ext != ".jsonl":
        return JSONResponse({"error": "Not a JSONL file"}, status_code=400)

    try:
        with artifact.open_text(errors="replace") as fh:
            head: list[str] = []
            for raw_line in fh:
                stripped = raw_line.strip()
                if stripped:
                    head.append(stripped)
                if len(head) >= 20:
                    break
        adapter = detect_adapter(head)

        # classify_by_ext intentionally remains core-only and returns
        # "unknown-jsonl" for plugin-owned adapters. This handler owns the
        # metaproc-domain mapping so runpool charts do not disappear when
        # kind detection is delegated to the plugin manifest.
        from metaproc.metabrowser_plugin.charts import (  # noqa: PLC0415 -- guarded import (optional dep / circular)
            extract_runpool_charts,
        )

        if adapter == "runpool":
            data = await asyncio.to_thread(extract_runpool_charts, target)
        elif adapter in ("claude", "gemini", "pi"):
            data = await asyncio.to_thread(extract_agent_charts_cached, target)
        else:
            data = {"summary": None, "charts": []}
    except (OSError, TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)

    return JSONResponse(data)


# ── /stats ──────────────────────────────────────────────────────


def stats_handler(request: Request) -> JSONResponse:
    """Return unified run stats as JSON (same as ``metaproc stats --json``)."""
    rel = request.query_params.get("path", "")
    target = resolve_directory(rel)
    if target is None:
        return JSONResponse({"error": "not a directory under root"}, status_code=400)

    try:
        run_stats = compute_run_stats(target, include_system=False)
        return JSONResponse(run_stats.model_dump(mode="json"))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=500)


# ── /resources ──────────────────────────────────────────────────

RESOURCES_FILENAME = "resources.json"


def _resources_cache_stale(run_dir: Path, cache_path: Path, events_path: Path) -> bool:
    """Return true when snapshot-backed reports are incomplete or locally stale."""
    from metaproc.engine.resource_finalization import (  # noqa: PLC0415 -- optional route
        resource_artifacts_need_recovery,
    )
    from metaproc.engine.resource_snapshot import (  # noqa: PLC0415 -- optional route
        read_resource_run_snapshot,
    )

    try:
        if read_resource_run_snapshot(run_dir) is not None:
            return resource_artifacts_need_recovery(run_dir)
    except Exception:  # noqa: BLE001 - malformed snapshot should surface as stale, not break browse
        return True
    if not events_path.exists() or not cache_path.exists():
        return False
    try:
        return events_path.stat().st_mtime_ns > cache_path.stat().st_mtime_ns
    except OSError:
        return False


def resources_handler(request: Request) -> JSONResponse:
    """Return the run's ``resources.json`` rollup.

    Query params:
      run_dir / path  — run directory (relative to served root).
      process         — required when refresh=1 or no cached file exists.
      refresh         — rebuild from evidence when truthy.
    """
    rel = request.query_params.get("run_dir", request.query_params.get("path", ""))
    process_rel = request.query_params.get("process", "")
    refresh_flag = request.query_params.get("refresh", "").lower() in ("1", "true", "yes")

    target = resolve_directory(rel)
    if target is None:
        return JSONResponse({"error": "not a directory under root"}, status_code=400)

    cache_path = resolve_path(str(target / RESOURCES_FILENAME))
    events_path = resolve_path(str(target / ".logs" / "resource-events.jsonl"))
    summary_path = resolve_path(str(target / "resource-usage-summary.md"))
    summary_schema_path = resolve_path(
        str(target / ".state" / "schemas" / "resource-usage-summary.v1.schema.yaml")
    )
    if (
        cache_path is None
        or events_path is None
        or summary_path is None
        or summary_schema_path is None
    ):
        return JSONResponse(
            {"error": "resources paths resolve outside the served root"},
            status_code=400,
        )
    if refresh_flag or not cache_path.exists():
        from metaproc.engine.resource_snapshot import (  # noqa: PLC0415 -- optional rebuild path
            read_resource_run_snapshot,
        )

        try:
            snapshot = read_resource_run_snapshot(target)
        except Exception as exc:  # noqa: BLE001 - malformed run metadata is a request error
            return JSONResponse({"error": f"invalid resource snapshot: {exc}"}, status_code=500)
        if snapshot is not None:
            from metaproc.io.orchestrator_lease import (  # noqa: PLC0415 -- optional rebuild path
                is_orchestrator_alive,
            )

            if is_orchestrator_alive(target):
                return JSONResponse(
                    {"error": "resource recovery is unavailable while the run is active"},
                    status_code=409,
                )
            from metaproc.engine.resource_finalization import (  # noqa: PLC0415 -- optional rebuild path
                finalize_run_resources,
            )
            from metaproc.models.resource_budget import (  # noqa: PLC0415 -- optional rebuild path
                FinalizationState,
            )
            from metaproc.models.resources import (  # noqa: PLC0415 -- optional rebuild path
                ResourcesDocument,
                ResourcesDocumentV2,
                read_resources_document_json,
            )

            outcome = FinalizationState.FAILED
            snapshot_bundle = None
            if process_rel:
                process_path = resolve_path(process_rel)
                if process_path is None:
                    return JSONResponse({"error": "process outside root"}, status_code=400)
                if not process_path.is_file():
                    return JSONResponse(
                        {"error": "process not found", "process": process_rel}, status_code=404
                    )
                snapshot_bundle = load_plan_bundle(process_path)
            if cache_path.exists():
                try:
                    prior = read_resources_document_json(cache_path.read_text())
                    if (
                        isinstance(prior, (ResourcesDocument, ResourcesDocumentV2))
                        and prior.finalization is not None
                    ):
                        outcome = prior.finalization.state
                except Exception:  # noqa: BLE001 - refresh intentionally repairs malformed cache
                    pass
            try:
                document = finalize_run_resources(
                    target,
                    outcome=outcome,
                    trigger="recovery",
                    bundle=snapshot_bundle,
                ).document
            except Exception as exc:  # noqa: BLE001 - HTTP boundary reports refresh failures
                return JSONResponse({"error": str(exc)}, status_code=500)
        elif not process_rel:
            return JSONResponse(
                {
                    "error": (
                        "process query param is required to build resources.json for a "
                        "legacy run without an immutable resource snapshot."
                    )
                },
                status_code=400,
            )
        else:
            process_path = resolve_path(process_rel)
            if process_path is None:
                return JSONResponse({"error": "process outside root"}, status_code=400)
            if not process_path.is_file():
                return JSONResponse(
                    {"error": "process not found", "process": process_rel}, status_code=404
                )
            from metaproc.engine.resource_rollup import (  # noqa: PLC0415 -- pre-existing local import; needs review
                build_resource_artifacts,
            )

            try:
                bundle = load_plan_bundle(process_path)
                result = build_resource_artifacts(
                    bundle=bundle,
                    run_dir=target,
                    run_id=target.name,
                    document_path=cache_path,
                    write=True,
                )
                document = result.document
            except Exception as exc:  # noqa: BLE001 - HTTP boundary reports rebuild failures
                return JSONResponse({"error": str(exc)}, status_code=500)
    else:
        from metaproc.models.resources import (  # noqa: PLC0415 -- pre-existing local import; needs review
            read_resources_document_json,
        )

        try:
            document = read_resources_document_json(cache_path.read_text())
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                {"error": f"cached resources.json is malformed: {exc!s}"},
                status_code=500,
            )

    payload = document.model_dump(mode="json", by_alias=True)
    payload["stale"] = _resources_cache_stale(target, cache_path, events_path)
    return JSONResponse(payload)


# ── /structure-report ───────────────────────────────────────────


def structure_report_handler(request: Request) -> JSONResponse:
    """Return a transient softschema structure report for a process spec."""
    process = request.query_params.get("process", "")
    run_dir_param = request.query_params.get("run_dir")
    target = resolve_path(process)
    if target is None or not target.is_file():
        return JSONResponse({"error": "Not found", "process": process}, status_code=404)

    run_dir: Path | None = None
    if run_dir_param:
        run_dir = resolve_directory(run_dir_param)
        if run_dir is None:
            return JSONResponse({"error": "run_dir not found or outside root"}, status_code=400)

    try:
        from metaproc.commands.helpers import (  # noqa: PLC0415 -- guarded import (optional dep / circular)
            load_process_spec,
        )
        from metaproc.plugins.discovery import (  # noqa: PLC0415 -- guarded import (optional dep / circular)
            discover_and_load_plugins,
            get_plugin_registry,
        )
        from metaproc.structure_report import (  # noqa: PLC0415 -- guarded import (optional dep / circular)
            build_structure_report,
        )

        discover_and_load_plugins()
        spec = load_process_spec(target)
        report = build_structure_report(
            process_path=target,
            spec=spec,
            registry=get_plugin_registry().softschemas,
            run_dir=run_dir,
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc), "process": process}, status_code=400)

    return JSONResponse(
        {
            "structure_report": report.model_dump(
                by_alias=True,
                exclude_none=True,
                mode="json",
            )
        }
    )
