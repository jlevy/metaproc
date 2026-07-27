"""metaproc gcp — GCP Batch job management commands.

Requires the ``[gcp-batch]`` optional extra:
    uv sync --extra gcp-batch

Commands:
    status  — Show status of GCP Batch jobs for a run (local dir or run-id)
    scale   — Update desired topology for an active cloud fan-out step
    logs    — Stream logs from Cloud Logging for a run (local dir or run-id)
    cancel  — Cancel running GCP Batch jobs for a run (local dir or run-id)
    runs    — List all active metaproc runs across the project
    archive — Sync a run directory to GCS for long-term storage
    cleanup — Delete old GCP Batch jobs in terminal states
    remote  — Run a metaproc command on the remote gateway host via SSH/IAP
    remote-run — Launch run-process in a tmux session on a remote host
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import typer
from strif import atomic_output_file

from metaproc.errors import CLIError
from metaproc.io import read_yaml_file
from metaproc.io.dispatch_manifest import read_dispatch_manifest
from metaproc.paths import step_state_dir as _step_state_dir

log = logging.getLogger(__name__)

from metaproc import paths as paths_mod
from metaproc.cli import app, get_output
from metaproc.commands.helpers import load_process_spec
from metaproc.commands.ssh_utils import build_ssh_command
from metaproc.config.env_vars import MetaprocEnv
from metaproc.engine.build_plan import build_plan
from metaproc.io import (
    iter_artifact_paths,
    iter_jsonl_objects,
    resolve_existing_artifact,
)
from metaproc.output import OutputManager
from metaproc.paths import (
    DISPATCH_MANIFEST_FILE,
    POOL_KILL_SENTINEL_FILE,
    POOL_STATUS_FILE,
    PROCESS_SPEC_SUFFIX,
    RUN_CONFIG_FILE,
    SCALE_OVERRIDE_FILE,
    SCALE_STATE_FILE,
    STATE_DIR,
)
from metaproc.plugins.discovery import get_bootstrap_env_vars
from metaproc.runpool.status import (
    ControllerStatus,
    ScaleBounds,
    ScaleOverride,
    ScaleState,
    read_scale_override,
    read_scale_state,
    read_status,
    write_scale_override,
    write_scale_state,
)

gcp_app = typer.Typer(
    name="gcp",
    help="GCP Batch job management (requires [gcp-batch] extra).",
    no_args_is_help=True,
)
app.add_typer(gcp_app)

# Register `gcp run` (defined in gcp_run.py to keep this file from growing).
# Imported after gcp_app is created because gcp_run.py registers via the
# returned function rather than via decorator.
# Guarded by try/except so `metaproc.commands.gcp` remains importable in
# environments without the [gcp-batch] extra (e.g. example_plugin CI):
# `gcp run` then drops out, and the other gcp subcommands surface the same
# friendly error via _require_gcp_batch() at invocation time.
try:
    from metaproc.commands.gcp_run import run_command as _gcp_run_command  # noqa: E402

    gcp_app.command("run")(_gcp_run_command)
except ImportError as exc:
    # Only swallow the specific "optional extra missing" case. A broken
    # top-level import inside gcp_run.py (typo, renamed symbol) would
    # otherwise silently hide the ``gcp run`` subcommand — we want those
    # failures to surface loudly at import time.
    # Also catches "google" / "google.protobuf": the [gcp-batch] extra installs
    # google-cloud-batch which pulls in google.protobuf transitively, so when
    # the extra is missing the entire `google` namespace package is absent.
    if exc.name not in {
        "google",
        "google.cloud.batch",
        "google.cloud.batch_v1",
        "google.protobuf",
    }:
        raise

_BATCH_RPC_TIMEOUT_S = 60.0


def _require_gcp_batch() -> None:
    """Raise a clear error if [gcp-batch] extra is not installed."""
    try:
        from google.cloud import (  # noqa: PLC0415, F401 -- optional [gcp-batch] dependency
            batch_v1,
        )
    except ImportError:
        typer.echo(
            "GCP Batch support requires the [gcp-batch] extra.\n"
            "Install with: uv sync --extra gcp-batch",
            err=True,
        )
        raise typer.Exit(code=1) from None


def _read_events(run_dir: Path) -> list[dict[str, object]]:
    """Read runpool events from the run directory."""
    events = []
    for events_file in _runpool_event_files(run_dir):
        events.extend(iter_jsonl_objects(events_file))
    return events


def _runpool_event_files(run_dir: Path) -> list[Path]:
    paths: list[Path] = []
    paths.append(paths_mod.runpool_events_for_read(run_dir))
    workers_root = paths_mod.runpool_logs_dir(run_dir) / paths_mod.WORKERS_SUBDIR
    if workers_root.is_dir():
        paths.extend(iter_artifact_paths(workers_root, f"*/{paths_mod.RUNPOOL_EVENTS_FILE}"))
    steps_root = paths_mod.runpool_logs_dir(run_dir) / paths_mod.STEPS_SUBDIR
    if steps_root.is_dir():
        paths.extend(iter_artifact_paths(steps_root, f"*/{paths_mod.RUNPOOL_EVENTS_FILE}"))
    if not paths_mod.is_v2_run_layout(run_dir):
        legacy_logs = paths_mod.run_logs_dir(run_dir)
        paths.extend(iter_artifact_paths(legacy_logs, f"worker-*/{paths_mod.POOL_EVENTS_FILE}"))
        legacy_steps = legacy_logs / paths_mod.STEPS_SUBDIR
        if legacy_steps.is_dir():
            paths.extend(iter_artifact_paths(legacy_steps, f"*/{paths_mod.POOL_EVENTS_FILE}"))

    seen: set[Path] = set()
    files: list[Path] = []
    for path in paths:
        path = resolve_existing_artifact(path)
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        files.append(path)
    return files


def _extract_job_names(events: list[dict[str, object]]) -> list[str]:
    """Extract GCP Batch job names from process_start events."""
    job_names = []
    for event in events:
        if event.get("event") == "process_start" and event.get("external_id"):
            ext_id = str(event["external_id"])
            # external_id is the full GCP Batch job name
            if "locations" in ext_id and "jobs" in ext_id:
                job_names.append(ext_id)
    return job_names


def _resolve_job_uids(job_names: list[str]) -> list[str]:
    """Look up Batch-assigned UIDs for a list of fully-qualified job names.

    Cloud Logging keys ``batch_task_logs`` entries on ``labels.job_uid`` and
    ``resource.labels.job_id`` (both set to the server-generated UID, not the
    human job_id embedded in the job name). Resolve via GetJob to get the
    UIDs needed by the log filter.
    """
    from google.cloud import batch_v1  # noqa: PLC0415 -- optional [gcp-batch] dependency

    client = batch_v1.BatchServiceClient()
    uids: list[str] = []
    for name in job_names:
        if not name.startswith("projects/"):
            continue
        try:
            job = client.get_job(name=name)
        except Exception:  # noqa: BLE001 -- tolerate individual lookup misses.
            continue
        uid = getattr(job, "uid", "")
        if uid:
            uids.append(str(uid))
    return uids


def _event_matches_item(event: dict[str, object], item: str) -> bool:
    label = str(event.get("label", ""))
    if not label:
        return False
    return label == item or label.split("=", 1)[-1] == item


def _is_run_dir(target: str) -> bool:
    """Return True if target is an existing directory (local run path)."""
    return Path(target).is_dir()


def _query_jobs_by_run_id(run_id: str, project: str, region: str) -> list[Any]:
    """Query Batch API for jobs matching a run-id label."""
    from google.cloud import batch_v1  # noqa: PLC0415 -- optional [gcp-batch] dependency

    from metaproc.cloud.gcp.batch_backend import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
        sanitize_label,
    )

    client = batch_v1.BatchServiceClient()
    parent = f"projects/{project}/locations/{region}"
    sanitized_id = sanitize_label(run_id)
    filter_str = f'labels.metaproc-run-id="{sanitized_id}"'
    request = batch_v1.ListJobsRequest(parent=parent, filter=filter_str)
    return list(client.list_jobs(request=request))


def _format_job_results(
    jobs: list[Any],
    *,
    run_id: str,
    failed_only: bool,
    as_json: bool,
    out: OutputManager,
) -> None:
    """Format and display job results (shared by both status modes)."""
    from google.cloud.batch_v1.types import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
        JobStatus,
    )

    results = []
    for job in jobs:
        labels = dict(job.labels)
        role = labels.get("metaproc-role", labels.get("metaproc-dispatch", "unknown"))
        state = JobStatus.State(job.status.state).name
        job_name = job.name
        job_id = job_name.split("/")[-1] if "/" in job_name else job_name

        if failed_only and state not in ("FAILED", "CANCELLED"):
            continue

        info = {
            "job_id": job_id,
            "role": role,
            "state": state,
            "step": labels.get("metaproc-step", ""),
            "variant": labels.get("metaproc-variant", ""),
            "worker_id": labels.get("metaproc-worker-id", ""),
        }
        results.append(info)

    # Sort: orchestrator first, then workers by worker_id.
    results.sort(key=lambda r: (0 if r["role"] == "orchestrator" else 1, r.get("worker_id", "")))

    if as_json:
        out.data(json.dumps(results, indent=2))
        return

    # Summary.
    orchestrators = [r for r in results if r["role"] == "orchestrator"]
    workers = [r for r in results if r["role"] != "orchestrator"]

    out.data(f"Run: {run_id}")
    out.data(f"Jobs: {len(results)} ({len(orchestrators)} orchestrator, {len(workers)} worker)")
    out.data("")

    states: dict[str, int] = {}
    for r in results:
        s = r["state"]
        states[s] = states.get(s, 0) + 1
    for state, count in sorted(states.items()):
        out.data(f"  {state}: {count}")
    out.data("")

    for r in results:
        role = r["role"]
        state = r["state"]
        job_id = r["job_id"]
        step = r.get("step", "")
        worker_id = r.get("worker_id", "")

        if role == "orchestrator":
            out.data(f"  [orchestrator] {job_id}: {state}")
        else:
            label = f"worker-{worker_id}" if worker_id else "worker"
            if step:
                label = f"{label} ({step})"
            out.data(f"  [{label}] {job_id}: {state}")


@gcp_app.command("status")
def gcp_status(
    target: str = typer.Argument(..., help="Run directory or run-id string"),
    failed_only: bool = typer.Option(False, "--failed", help="Show only failed/cancelled jobs"),
    project: str = typer.Option("", "--project", help="GCP project (default: from env)"),
    region: str = typer.Option("us-central1", "--region", help="GCP region"),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Show Batch job status for a run.

    If <target> is an existing directory: read local runpool events, query jobs by name.
    If <target> is a string: query Batch API by metaproc-run-id label.
    """
    _require_gcp_batch()
    from google.cloud import batch_v1  # noqa: PLC0415 -- optional [gcp-batch] dependency

    out = get_output()

    if _is_run_dir(target):
        # Local run directory mode: read events, get job names, fetch each.
        run_dir = Path(target)
        events = _read_events(run_dir)
        job_names = _extract_job_names(events)

        if not job_names:
            out.progress("No GCP Batch jobs found in run events.")
            raise typer.Exit(code=0)

        client = batch_v1.BatchServiceClient()
        jobs = []
        for job_name in job_names:
            try:
                job = client.get_job(batch_v1.GetJobRequest(name=job_name))
                jobs.append(job)
            except Exception as exc:
                out.progress(f"  Warning: could not fetch {job_name}: {exc}")

        if not jobs:
            out.progress("No GCP Batch jobs could be fetched.")
            raise typer.Exit(code=0)

        # Extract run_id from the first job's labels.
        first_labels = dict(jobs[0].labels)
        run_id = first_labels.get("metaproc-run-id", str(run_dir))
    else:
        # Run-id mode: query Batch API by label.
        run_id = target
        effective_project = project or MetaprocEnv.METAPROC_GCP_PROJECT.read_str(default="")
        if not effective_project:
            raise CLIError("--project or METAPROC_GCP_PROJECT is required")

        try:
            jobs = _query_jobs_by_run_id(run_id, effective_project, region)
        except Exception as exc:
            raise CLIError(f"Failed to list Batch jobs: {exc}") from exc

        if not jobs:
            out.progress(f"No Batch jobs found for run-id '{run_id}'.")
            raise typer.Exit(code=0)

    _format_job_results(jobs, run_id=run_id, failed_only=failed_only, as_json=as_json, out=out)


@gcp_app.command("scale")
def gcp_scale(
    target: str = typer.Argument(..., help="Run directory or run-id string"),
    step: str = typer.Option(..., "--step", help="Fan-out step id to scale"),
    num_workers: int | None = typer.Option(  # noqa: UP007
        None, "--num-workers", min=1, help="Desired active worker count"
    ),
    max_concurrency: int | None = typer.Option(  # noqa: UP007
        None, "--max-concurrency", min=1, help="Desired per-worker max concurrency"
    ),
    region: str = typer.Option("us-central1", "--region", help="GCP region"),
    reconcile: bool = typer.Option(
        True,
        "--reconcile/--no-reconcile",
        help="Immediately reconcile upward worker changes when possible",
    ),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt"),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Update desired topology for an active cloud fan-out step."""
    from metaproc.cloud.gcp.worker_dispatch import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
        WorkerDispatchConfig,
        build_gcp_config_from_env,
        reconcile_dispatched_workers,
    )

    if num_workers is None and max_concurrency is None:
        raise CLIError("pass at least one of --num-workers or --max-concurrency")

    out = get_output()
    run_dir = _resolve_scale_run_dir(target, step=step)

    # Confirmation prompt for scale operations that affect live workers.
    if not yes:
        action_parts: list[str] = []
        if num_workers is not None:
            action_parts.append(f"workers={num_workers}")
        if max_concurrency is not None:
            action_parts.append(f"max_concurrency={max_concurrency}")
        typer.confirm(
            f"Scale step '{step}' to {', '.join(action_parts)}?",
            abort=True,
        )

    scale_state = _build_desired_scale_state(
        run_dir=run_dir,
        step=step,
        num_workers=num_workers,
        max_concurrency=max_concurrency,
    )
    write_scale_state(_step_scale_state_path(run_dir, step), scale_state)

    updated_workers: list[str] = []
    if max_concurrency is not None:
        updated_workers = _apply_live_worker_cap_override(run_dir, max_concurrency)

    reconcile_note = ""
    if reconcile and num_workers is not None:
        try:
            process_path, variables, item_contexts, each, variant = _load_scale_reconcile_context(
                run_dir,
                step=step,
            )
            effective_spot = _infer_scale_spot_from_manifest(
                run_dir,
                step=step,
            )
            if effective_spot is None:
                effective_spot = _spot_from_env(default=True)

            gcp_config = build_gcp_config_from_env(spot=effective_spot)
            dispatch_config = WorkerDispatchConfig(
                gcp=gcp_config,
                num_workers=scale_state.desired_workers or 1,
                max_concurrency=scale_state.desired_max_concurrency or 1,
                spot=effective_spot,
                variant=variant,
            )
            process_spec_rel = _resolve_process_spec_rel(process_path)
            nfs_run_dir = str(run_dir) if gcp_config.filestore_server else ""
            asyncio.run(
                reconcile_dispatched_workers(
                    run_dir=run_dir,
                    step=step,
                    item_contexts=item_contexts,
                    each=each,
                    config=dispatch_config,
                    process_spec_rel=process_spec_rel,
                    variables=variables,
                    out=out,
                    nfs_run_dir=nfs_run_dir,
                    wait_for_completion=False,
                )
            )
            reconcile_note = "reconcile attempted"
        except CLIError:
            raise
        except Exception as exc:
            from google.api_core.exceptions import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
                GoogleAPICallError,
            )

            if not isinstance(exc, GoogleAPICallError):
                raise
            reconcile_note = f"reconcile deferred: {exc}"
            log.warning(
                "Immediate scale reconcile failed (GCP API error); "
                "desired topology written to scale-state.yaml — "
                "operator must rerun reconcile",
                exc_info=True,
            )

    payload = {
        "run_dir": str(run_dir),
        "step": step,
        "desired_workers": scale_state.desired_workers,
        "desired_max_concurrency": scale_state.desired_max_concurrency,
        "generation": scale_state.generation,
        "updated_worker_overrides": updated_workers,
        "reconcile": reconcile_note or ("skipped" if not reconcile or num_workers is None else ""),
    }
    if as_json:
        out.data(json.dumps(payload, indent=2))
        return

    out.data(f"Run dir: {run_dir}")
    out.data(f"Step: {step}")
    out.data(
        "Desired topology: "
        f"workers={scale_state.desired_workers} "
        f"max_concurrency={scale_state.desired_max_concurrency} "
        f"generation={scale_state.generation}"
    )
    if updated_workers:
        out.data(f"Live cap overrides: {', '.join(updated_workers)}")
    if reconcile_note:
        out.data(reconcile_note)


def _spot_from_env(*, default: bool) -> bool:
    raw = MetaprocEnv.METAPROC_SPOT.read_str(default="").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


def _infer_scale_spot_from_manifest(
    run_dir: Path,
    *,
    step: str,
) -> bool | None:
    """Infer the worker provisioning model for a live scale-up.

    We prefer the already-dispatched worker jobs over ambient operator env vars
    so scaled workers inherit the live run's original Spot/Standard policy.
    """
    from google.cloud import batch_v1  # noqa: PLC0415 -- optional [gcp-batch] dependency
    from google.cloud.batch_v1.types import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
        AllocationPolicy,
    )

    manifest = read_dispatch_manifest(run_dir, step)
    if manifest is None:
        return None

    raw_workers = manifest.get("workers")
    if not isinstance(raw_workers, list):
        return None

    job_names = [
        str(job_name)
        for worker in raw_workers
        if isinstance(worker, dict) and isinstance(job_name := worker.get("job_name"), str)
    ]
    if not job_names:
        return None

    client = batch_v1.BatchServiceClient()
    for job_name in job_names:
        try:
            job = client.get_job(
                batch_v1.GetJobRequest(name=job_name),
                timeout=_BATCH_RPC_TIMEOUT_S,
            )
        except Exception:
            log.warning("Failed to inspect worker job %s for scale spot inference", job_name)
            continue

        allocation_policy = getattr(job, "allocation_policy", None)
        instances = getattr(allocation_policy, "instances", None) or []
        if not instances:
            continue
        policy = getattr(instances[0], "policy", None)
        provisioning_model = getattr(policy, "provisioning_model", None)
        if provisioning_model is None:
            continue
        return provisioning_model == AllocationPolicy.ProvisioningModel.SPOT

    return None


def _resolve_job_names_and_project(target: str, project: str, region: str) -> tuple[list[str], str]:
    """Resolve target to a list of job names and the GCP project.

    If target is a directory, read local events. If it's a run-id, query Batch API.
    Returns (job_names, project_id).
    """
    if _is_run_dir(target):
        run_dir = Path(target)
        events = _read_events(run_dir)
        job_names = _extract_job_names(events)
        if not job_names:
            return [], ""
        # Extract project from the first job name.
        match = re.match(r"projects/([^/]+)/", job_names[0])
        resolved_project = match.group(1) if match else ""
        return job_names, resolved_project
    # Run-id mode: query Batch API by label.
    effective_project = project or MetaprocEnv.METAPROC_GCP_PROJECT.read_str(default="")
    if not effective_project:
        raise CLIError("--project or METAPROC_GCP_PROJECT is required")

    jobs = _query_jobs_by_run_id(target, effective_project, region)
    job_names = [j.name for j in jobs]
    return job_names, effective_project


def _candidate_runs_roots() -> list[Path]:
    """Return local run-root candidates for run-id based commands."""
    roots: list[Path] = []
    runs_dir = MetaprocEnv.RUNS_DIR.read_str(default="")
    if runs_dir:
        roots.append(Path(runs_dir))
    filestore_mount = MetaprocEnv.METAPROC_GCP_FILESTORE_MOUNT_PATH.read_str(default="")
    if filestore_mount:
        roots.append(Path(filestore_mount) / "runs")

    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def _resolve_scale_run_dir(target: str, *, step: str) -> Path:
    """Resolve a scale target to a local process run directory."""
    if _is_run_dir(target):
        return Path(target).resolve()

    for runs_root in _candidate_runs_roots():
        run_root = runs_root / target
        if not run_root.exists():
            continue
        if (run_root / STATE_DIR / RUN_CONFIG_FILE).exists():
            return run_root.resolve()

        process_candidates: list[Path] = []
        for child in sorted(run_root.iterdir()):
            if not child.is_dir():
                continue
            if not (child / STATE_DIR / RUN_CONFIG_FILE).exists():
                continue
            step_state_path = child / STATE_DIR / "steps" / step
            if (step_state_path / DISPATCH_MANIFEST_FILE).exists() or (
                step_state_path / SCALE_STATE_FILE
            ).exists():
                return child.resolve()
            process_candidates.append(child.resolve())

        if len(process_candidates) == 1:
            return process_candidates[0]
        if len(process_candidates) > 1:
            raise CLIError(
                f"run-id '{target}' resolves to multiple process run dirs under {run_root}; "
                "pass the local run directory explicitly"
            )

    searched = ", ".join(str(root) for root in _candidate_runs_roots()) or "RUNS_DIR"
    raise CLIError(
        f"could not resolve run-id '{target}' to a local run directory; searched {searched}"
    )


def _read_run_config(run_dir: Path) -> dict[str, object]:
    """Read run-config.yaml for a process run directory."""

    path = run_dir / STATE_DIR / RUN_CONFIG_FILE
    if not path.exists():
        raise CLIError(f"run-config.yaml not found under {run_dir}")
    raw = read_yaml_file(path)
    if not isinstance(raw, dict):
        raise CLIError(f"corrupt run-config.yaml: {path}")
    return raw


def _resolve_process_spec_for_run(run_config: dict[str, object]) -> Path:
    """Resolve the local process spec file for a run-config.

    Prefers the canonical ``process_spec`` (file path). Falls back to legacy
    ``process_dir`` for pre-rename run-configs, in which case it looks for a
    ``*.process.md`` file inside that directory. Finally, falls back to the
    process name and scans the repo for a matching spec.
    """
    process_spec_raw = str(run_config.get("process_spec", "")).strip()
    if process_spec_raw:
        candidate = Path(process_spec_raw)
        if candidate.exists():
            return candidate

    # Legacy: run-config has process_dir only (pre-rename).
    process_dir_raw = str(run_config.get("process_dir", "")).strip()
    if process_dir_raw:
        dir_candidate = Path(process_dir_raw)
        if dir_candidate.is_dir():
            spec_files = sorted(dir_candidate.glob(f"*{PROCESS_SPEC_SUFFIX}"))
            if len(spec_files) == 1:
                return spec_files[0]

    process_name = str(run_config.get("process", "")).strip()
    if not process_name:
        raise CLIError("run-config.yaml is missing process metadata")

    repo_root = Path.cwd().resolve()
    matches: list[Path] = []
    # Scope search to known top-level process directories, not the whole repo.
    search_dirs = [
        d
        for d in repo_root.iterdir()
        if d.is_dir()
        and not d.name.startswith(".")
        and d.name not in {"node_modules", "__pycache__"}
    ]
    for search_dir in search_dirs:
        for process_path in search_dir.rglob(f"*{PROCESS_SPEC_SUFFIX}"):
            spec = load_process_spec(process_path)
            if spec.name == process_name:
                matches.append(process_path)

    if len(matches) == 1:
        return matches[0]

    raise CLIError(
        f"could not resolve local process spec for process={process_name!r}; "
        "pass a local run directory created on this host or ensure the repo checkout matches"
    )


def _resolve_process_spec_rel(process_path: Path) -> str:
    """Return a repo-relative spec file path for cloud worker env vars."""
    resolved = process_path.resolve()
    repo_root = Path.cwd().resolve()
    for parent in [repo_root, *repo_root.parents]:
        if (parent / ".git").exists():
            repo_root = parent
            break
    try:
        return str(resolved.relative_to(repo_root))
    except ValueError:
        return str(resolved)


def _aggregate_worker_controller(run_dir: Path) -> tuple[ControllerStatus | None, dict[str, int]]:
    """Aggregate live controller state across worker-local status/scale-state files."""
    state_root = paths_mod.run_state_dir(run_dir)
    if not state_root.exists():
        return None, {}

    controllers: list[ControllerStatus] = []
    live_caps: dict[str, int] = {}
    bottleneck_counts: dict[str, int] = {}
    manual = False

    worker_dirs = []
    workers_root = state_root / paths_mod.WORKERS_SUBDIR
    if workers_root.is_dir():
        worker_dirs.extend(sorted(workers_root.glob("worker-*")))
    if not paths_mod.is_v2_run_layout(run_dir):
        worker_dirs.extend(sorted(state_root.glob("worker-*")))

    for worker_dir in worker_dirs:
        if not worker_dir.is_dir():
            continue
        worker_id = worker_dir.name
        status_path = worker_dir / POOL_STATUS_FILE
        scale_state_path = worker_dir / SCALE_STATE_FILE

        controller: ControllerStatus | None = None
        if scale_state_path.exists():
            try:
                controller = read_scale_state(scale_state_path).controller
            except (OSError, ValueError) as exc:
                log.warning("Failed to read scale state for %s: %s", worker_id, exc)
                controller = None
        if controller is None and status_path.exists():
            try:
                status = read_status(status_path)
            except (OSError, ValueError) as exc:
                log.warning("Failed to read pool status for %s: %s", worker_id, exc)
                status = None
            if status is not None:
                controller = status.controller
                live_caps[worker_id] = status.max_concurrency
        elif status_path.exists():
            try:
                live_caps[worker_id] = read_status(status_path).max_concurrency
            except (OSError, ValueError) as exc:
                log.warning("Failed to read pool status for %s: %s", worker_id, exc)

        if controller is None:
            continue
        controllers.append(controller)
        manual = manual or controller.mode == "manual"
        bottleneck_counts[controller.bottleneck] = (
            bottleneck_counts.get(controller.bottleneck, 0) + 1
        )

    if not controllers:
        return None, live_caps

    bottleneck = max(bottleneck_counts.items(), key=lambda item: item[1])[0]
    return (
        ControllerStatus(
            mode="manual" if manual else "adaptive",
            operator_cap=sum(controller.operator_cap for controller in controllers),
            effective_target=sum(controller.effective_target for controller in controllers),
            memory_ceiling=sum(controller.memory_ceiling for controller in controllers),
            provider_ceiling=sum(controller.provider_ceiling for controller in controllers),
            bottleneck=bottleneck,
            recent_rate_limits=sum(controller.recent_rate_limits for controller in controllers),
            pending_retries=sum(controller.pending_retries for controller in controllers),
        ),
        live_caps,
    )


def _step_scale_state_path(run_dir: Path, step: str) -> Path:
    """Return ``<run>/.state/steps/<step>/scale-state.yaml``."""

    return _step_state_dir(run_dir, step) / SCALE_STATE_FILE


def _build_desired_scale_state(
    *,
    run_dir: Path,
    step: str,
    num_workers: int | None,
    max_concurrency: int | None,
) -> ScaleState:
    """Build the next desired topology state for a step."""
    existing_path = _step_scale_state_path(run_dir, step)
    existing = read_scale_state(existing_path) if existing_path.exists() else None
    controller, live_caps = _aggregate_worker_controller(run_dir)

    desired_workers = num_workers
    if desired_workers is None and existing is not None:
        desired_workers = existing.desired_workers
    if desired_workers is None:
        desired_workers = max(1, len(live_caps))

    desired_cap = max_concurrency
    if desired_cap is None and existing is not None:
        desired_cap = existing.desired_max_concurrency
    if desired_cap is None and live_caps:
        desired_cap = max(live_caps.values())
    if desired_cap is None and controller is not None and live_caps:
        desired_cap = max(1, controller.operator_cap // max(1, len(live_caps)))
    if desired_cap is None:
        desired_cap = 1

    effective_controller = existing.controller if existing is not None else controller
    if effective_controller is None:
        effective_controller = ControllerStatus(
            operator_cap=desired_cap,
            effective_target=desired_cap,
            memory_ceiling=desired_cap,
            provider_ceiling=desired_cap,
            bottleneck="operator-capped",
        )

    bounds = (
        existing.bounds if existing is not None and existing.bounds is not None else ScaleBounds()
    )
    bounds.max_workers = max(bounds.max_workers or 0, desired_workers, len(live_caps)) or None
    bounds.max_concurrency = max(bounds.max_concurrency or 0, desired_cap) or None

    return ScaleState(
        updated_at=datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S"),
        controller=effective_controller,
        desired_workers=desired_workers,
        desired_max_concurrency=desired_cap,
        generation=(existing.generation + 1) if existing is not None else 1,
        bounds=bounds,
    )


def _apply_live_worker_cap_override(run_dir: Path, max_concurrency: int) -> list[str]:
    """Apply live operator-cap overrides to active worker state dirs."""
    updated_workers: list[str] = []
    state_root = paths_mod.run_state_dir(run_dir)
    if not state_root.exists():
        return updated_workers

    worker_dirs = []
    workers_root = state_root / paths_mod.WORKERS_SUBDIR
    if workers_root.is_dir():
        worker_dirs.extend(sorted(workers_root.glob("worker-*")))
    if not paths_mod.is_v2_run_layout(run_dir):
        worker_dirs.extend(sorted(state_root.glob("worker-*")))

    for worker_dir in worker_dirs:
        if not worker_dir.is_dir():
            continue
        override_path = worker_dir / SCALE_OVERRIDE_FILE
        current = read_scale_override(override_path) if override_path.exists() else ScaleOverride()
        write_scale_override(
            override_path,
            ScaleOverride(mode=current.mode, operator_cap=max_concurrency),
        )
        updated_workers.append(worker_dir.name)
    return updated_workers


def _load_scale_reconcile_context(
    run_dir: Path,
    *,
    step: str,
) -> tuple[Path, dict[str, str], list[dict[str, str]], str, str]:
    """Load the context needed to reconcile a scaled cloud worker step."""

    run_config = _read_run_config(run_dir)
    process_path = _resolve_process_spec_for_run(run_config)
    spec = load_process_spec(process_path)

    raw_variables = run_config.get("variables", {})
    if not isinstance(raw_variables, dict):
        raise CLIError("run-config.yaml is missing variables")
    variables = {str(key): str(value) for key, value in raw_variables.items()}

    resolved = build_plan(spec, variables, process_path=process_path)
    target = next(
        (resolved_step for resolved_step in resolved.steps if resolved_step.step_id == step), None
    )
    if target is None or target.fan_out is None:
        raise CLIError(f"step '{step}' is not a fan-out step in the current process plan")

    manifest = read_dispatch_manifest(run_dir, step)
    variant = ""
    if manifest is not None:
        raw_variant = manifest.get("variant", "")
        if isinstance(raw_variant, str):
            variant = raw_variant
    if not variant:
        variant = str(run_config.get("variant", "") or "")

    return process_path, variables, target.fan_out.items, target.fan_out.bind, variant


_VALID_LOG_ROLES = ("orchestrator", "worker", "all")


def _build_logs_filter(
    *,
    job_uids: list[str],
    project: str,
    include_agent_logs: bool = False,
    errors_only: bool = False,
    since_timestamp: str = "",
) -> str:
    """Compose a Cloud Logging filter string for metaproc Batch logs.

    The default filter matches only ``batch_task_logs`` (container stdout);
    noisy VM agent heartbeats are excluded. Pass ``include_agent_logs=True``
    to also match ``batch_agent_logs`` (useful when the container never
    starts and you need to diagnose the bootstrap).

    Role / worker filtering is applied upstream by restricting ``job_uids``
    to the matching job set; that keeps the server-side filter simple and
    avoids relying on Batch label propagation into log entries.
    ``since_timestamp`` (RFC3339) is used by ``--follow`` to narrow polls.
    """
    uid_clause = "(" + " OR ".join(f'labels."job_uid"="{uid}"' for uid in job_uids) + ")"

    task_log_filter = (
        "("
        + " AND ".join(
            [
                'resource.type="batch.googleapis.com/Job"',
                'logName:"batch_task_logs"',
                uid_clause,
            ]
        )
        + ")"
    )
    log_filters = [task_log_filter]
    if include_agent_logs:
        log_filters.append(
            "("
            + " AND ".join(
                [
                    f'logName="projects/{project}/logs/batch_agent_logs"',
                    uid_clause,
                ]
            )
            + ")"
        )

    filter_parts = [
        "(" + " OR ".join(log_filters) + ")" if len(log_filters) > 1 else log_filters[0]
    ]

    if errors_only:
        filter_parts.append("severity>=ERROR")
    if since_timestamp:
        filter_parts.append(f'timestamp>="{since_timestamp}"')

    return " AND ".join(filter_parts)


def _format_log_entry(entry: Any) -> str:
    """Format a Cloud Logging entry as a single display line."""
    payload = entry.payload if isinstance(entry.payload, str) else str(entry.payload)
    ts = entry.timestamp.isoformat() if entry.timestamp else ""
    severity = entry.severity or ""
    return f"[{ts}] [{severity}] {payload}"


@gcp_app.command("logs")
def gcp_logs(
    target: str = typer.Argument(..., help="Run directory or run-id string"),
    item: str = typer.Option("", "--item", help="Filter to a specific item label"),
    errors_only: bool = typer.Option(False, "--errors", help="Show only ERROR+ severity"),
    limit: int = typer.Option(100, "--limit", help="Maximum number of log entries"),
    project: str = typer.Option("", "--project", help="GCP project (default: from env)"),
    region: str = typer.Option("us-central1", "--region", help="GCP region"),
    role: str = typer.Option(
        "all",
        "--role",
        help="Filter to Batch jobs with metaproc-role label: orchestrator, worker, or all.",
    ),
    worker: int | None = typer.Option(  # noqa: UP007
        None,
        "--worker",
        help="Pin to a single worker index (requires --role worker).",
    ),
    follow: bool = typer.Option(
        False, "--follow", "-f", help="Tail mode: poll every 10s, de-dup on insertId."
    ),
    include_agent_logs: bool = typer.Option(
        False,
        "--include-agent-logs",
        help="Include batch_agent_logs (VM agent heartbeats). "
        "Use when diagnosing bootstrap failures where the container never starts.",
    ),
) -> None:
    """Stream logs from Cloud Logging for a run's GCP Batch jobs.

    Resolves Batch job IDs from run events (for local run directories) or
    from the ``metaproc-run-id`` label (for run-id strings), then filters
    Cloud Logging on those jobs. By default only container stdout
    (``batch_task_logs``) is included; pass ``--include-agent-logs`` to
    include VM agent startup logs (useful for early bootstrap failures
    such as NFS mount errors).

    Use ``--role orchestrator`` (or ``worker``) to narrow to one side of
    the split. Combine with ``--worker N`` to pin to a single worker.
    Use ``--follow`` to tail new entries as they arrive.
    """
    _require_gcp_batch()
    from google.cloud import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
        logging as cloud_logging,
    )

    out = get_output()

    if role not in _VALID_LOG_ROLES:
        raise CLIError(f"--role must be one of: {', '.join(_VALID_LOG_ROLES)}")

    worker_index = worker
    if worker_index is not None and role != "worker":
        raise CLIError("--worker requires --role worker")

    # Resolve project — from flag, env, or local events. No Batch API call.
    resolved_project = project or MetaprocEnv.METAPROC_GCP_PROJECT.read_str(default="")
    if not resolved_project and _is_run_dir(target):
        events = _read_events(Path(target))
        job_names = _extract_job_names(events)
        if job_names:
            match = re.match(r"projects/([^/]+)/", job_names[0])
            if match:
                resolved_project = match.group(1)
    if not resolved_project:
        raise CLIError("--project or METAPROC_GCP_PROJECT is required")

    if _is_run_dir(target):
        events = _read_events(Path(target))
        if item:
            events = [event for event in events if _event_matches_item(event, item)]

        job_names = list(dict.fromkeys(_extract_job_names(events)))
        if not job_names:
            if item:
                out.progress(f"No GCP Batch jobs found in run events for item '{item}'.")
            else:
                out.progress("No GCP Batch jobs found in run events.")
            raise typer.Exit(code=0)

        # batch_task_logs entries key on the Batch-assigned UID, not the
        # dispatcher-chosen job name. Fetch UIDs for the recorded job names.
        job_uids = _resolve_job_uids(job_names)
        if not job_uids:
            out.progress("No Batch job UIDs resolved for recorded job names.")
            raise typer.Exit(code=0)
    else:
        run_id = target
        jobs = _query_jobs_by_run_id(run_id, resolved_project, region)
        if role != "all":
            jobs = [
                job
                for job in jobs
                if (getattr(job, "labels", {}) or {}).get("metaproc-role") == role
            ]
        if worker_index is not None:
            jobs = [
                job
                for job in jobs
                if (getattr(job, "labels", {}) or {}).get("metaproc-worker-id") == str(worker_index)
            ]
        if not jobs:
            scope = f"run-id '{run_id}'"
            if role != "all":
                scope = f"{scope} with role '{role}'"
            if worker_index is not None:
                scope = f"{scope}, worker {worker_index}"
            out.progress(f"No Batch jobs found for {scope}.")
            raise typer.Exit(code=0)

        job_uids = [str(job.uid) for job in jobs if getattr(job, "uid", "")]
        if not job_uids:
            out.progress("Batch returned jobs without UIDs; cannot query logs.")
            raise typer.Exit(code=0)

    client = cloud_logging.Client(project=resolved_project)

    if follow:
        _follow_logs(
            client=client,
            out=out,
            job_uids=job_uids,
            project=resolved_project,
            include_agent_logs=include_agent_logs,
            errors_only=errors_only,
        )
        return

    filter_str = _build_logs_filter(
        job_uids=job_uids,
        project=resolved_project,
        include_agent_logs=include_agent_logs,
        errors_only=errors_only,
    )

    entries = list(
        client.list_entries(
            filter_=filter_str,
            order_by="timestamp desc",
            max_results=limit,
        )
    )

    if not entries:
        out.progress("No log entries found.")
        raise typer.Exit(code=0)

    for entry in entries:
        out.data(_format_log_entry(entry))


_FOLLOW_POLL_SECONDS = 10.0
_FOLLOW_FRESHNESS_SECONDS = 10.0
_FOLLOW_DEDUP_CACHE_SIZE = 2000


def _follow_logs(
    *,
    client: Any,
    out: OutputManager,
    job_uids: list[str],
    project: str,
    include_agent_logs: bool,
    errors_only: bool,
) -> None:
    """Tail Cloud Logging entries matching the filter, polling every ~10s.

    On each iteration we query with ``timestamp>=(now-freshness)`` and drop
    entries we've already printed (tracked by ``insertId``). Runs until the
    user interrupts (Ctrl-C).
    """
    seen_ids: set[str] = set()
    seen_order: deque[str] = deque(maxlen=_FOLLOW_DEDUP_CACHE_SIZE)
    # Start one freshness window in the past to catch recent entries.
    last_poll = datetime.now(tz=UTC) - timedelta(seconds=_FOLLOW_FRESHNESS_SECONDS)

    try:
        while True:
            since = last_poll - timedelta(seconds=_FOLLOW_FRESHNESS_SECONDS)
            filter_str = _build_logs_filter(
                job_uids=job_uids,
                project=project,
                include_agent_logs=include_agent_logs,
                errors_only=errors_only,
                since_timestamp=since.isoformat().replace("+00:00", "Z"),
            )
            last_poll = datetime.now(tz=UTC)

            entries = list(
                client.list_entries(
                    filter_=filter_str,
                    order_by="timestamp asc",
                )
            )
            for entry in entries:
                insert_id = getattr(entry, "insert_id", "") or ""
                if insert_id and insert_id in seen_ids:
                    continue
                out.data(_format_log_entry(entry))
                if insert_id:
                    # deque(maxlen=N) auto-evicts the oldest entry; mirror
                    # that into the lookup set to keep them in sync.
                    if len(seen_order) == seen_order.maxlen:
                        seen_ids.discard(seen_order[0])
                    seen_order.append(insert_id)
                    seen_ids.add(insert_id)

            time.sleep(_FOLLOW_POLL_SECONDS)
    except KeyboardInterrupt:
        out.progress("Stopped following logs.")


@gcp_app.command("cancel")
def gcp_cancel(
    target: str = typer.Argument(..., help="Run directory or run-id string"),
    confirm: bool = typer.Option(False, "--yes", help="Skip confirmation prompt"),
    project: str = typer.Option("", "--project", help="GCP project (default: from env)"),
    region: str = typer.Option("us-central1", "--region", help="GCP region"),
) -> None:
    """Cancel all running/queued/scheduled Batch jobs for a run.

    If <target> is an existing directory: read local runpool events, extract job names.
    If <target> is a string: query Batch API by metaproc-run-id label.
    Writes a kill sentinel if a local run directory exists.
    """
    _require_gcp_batch()
    from google.cloud import batch_v1  # noqa: PLC0415 -- optional [gcp-batch] dependency
    from google.cloud.batch_v1.types import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
        JobStatus,
    )

    out = get_output()

    job_names, _ = _resolve_job_names_and_project(target, project, region)

    if not job_names:
        out.progress("No GCP Batch jobs found.")
        raise typer.Exit(code=0)

    client = batch_v1.BatchServiceClient()

    # Filter to running/queued jobs only.
    running_jobs = []
    for job_name in job_names:
        try:
            job = client.get_job(batch_v1.GetJobRequest(name=job_name))
            if job.status.state in (
                JobStatus.State.RUNNING,
                JobStatus.State.QUEUED,
                JobStatus.State.SCHEDULED,
            ):
                running_jobs.append(job_name)
        except Exception:
            log.warning("Failed to query job %s", job_name, exc_info=True)

    if not running_jobs:
        out.progress("No running GCP Batch jobs to cancel.")
        raise typer.Exit(code=0)

    out.data(f"Found {len(running_jobs)} running GCP Batch job(s).")

    if not confirm:
        response = typer.confirm(f"Cancel {len(running_jobs)} job(s)?")
        if not response:
            raise typer.Abort()

    cancelled = 0
    for job_name in running_jobs:
        try:
            client.cancel_job(batch_v1.CancelJobRequest(name=job_name))
            out.data(f"  Cancelled: {job_name}")
            cancelled += 1
        except Exception as exc:
            out.data(f"  Failed to cancel {job_name}: {exc}")

    out.data(f"\nCancelled {cancelled}/{len(running_jobs)} jobs.")

    # Write kill sentinel if local run directory exists.
    if _is_run_dir(target):
        run_dir = Path(target)
        sentinel_dir = run_dir / STATE_DIR
        sentinel_dir.mkdir(parents=True, exist_ok=True)
        sentinel_path = sentinel_dir / POOL_KILL_SENTINEL_FILE
        sentinel_body = (
            f"reason: gcp cancel\n"
            f"cancelled_at: {datetime.now(UTC).isoformat(timespec='seconds')}\n"
            f"jobs_cancelled: {cancelled}\n"
        )
        with atomic_output_file(sentinel_path) as tmp_path:
            tmp_path.write_text(sentinel_body)
        out.data(f"Wrote pool kill sentinel: {sentinel_path}")


@gcp_app.command("runs")
def gcp_runs(
    project: str = typer.Option("", "--project", help="GCP project (default: from env)"),
    region: str = typer.Option("us-central1", "--region", help="GCP region"),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """List all active metaproc runs across the project.

    Queries Batch API for all jobs with a metaproc-run-id label and groups
    them by run. This is the "what's happening now?" command.
    """
    _require_gcp_batch()

    from google.cloud import batch_v1  # noqa: PLC0415 -- optional [gcp-batch] dependency
    from google.cloud.batch_v1.types import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
        JobStatus,
    )

    out = get_output()

    effective_project = project or MetaprocEnv.METAPROC_GCP_PROJECT.read_str(default="")
    if not effective_project:
        raise CLIError("--project or METAPROC_GCP_PROJECT is required")

    client = batch_v1.BatchServiceClient()
    parent = f"projects/{effective_project}/locations/{region}"

    # List all jobs that have a metaproc-run-id label (any value).
    # Batch API filter doesn't support "label exists" directly, so we list
    # all jobs and filter client-side.
    try:
        request = batch_v1.ListJobsRequest(parent=parent)
        all_jobs = list(client.list_jobs(request=request))
    except Exception as exc:
        raise CLIError(f"Failed to list Batch jobs: {exc}") from exc

    # Group by run-id.
    runs: dict[str, list[dict[str, str]]] = defaultdict(list)
    for job in all_jobs:
        labels = dict(job.labels)
        run_id = labels.get("metaproc-run-id", "")
        if not run_id:
            continue

        role = labels.get("metaproc-role", labels.get("metaproc-dispatch", "unknown"))
        state = JobStatus.State(job.status.state).name
        job_name = job.name
        job_id = job_name.split("/")[-1] if "/" in job_name else job_name

        runs[run_id].append(
            {
                "job_id": job_id,
                "role": role,
                "state": state,
                "step": labels.get("metaproc-step", ""),
                "variant": labels.get("metaproc-variant", ""),
                "worker_id": labels.get("metaproc-worker-id", ""),
            }
        )

    if not runs:
        out.progress("No metaproc runs found.")
        raise typer.Exit(code=0)

    if as_json:
        out.data(json.dumps(dict(runs), indent=2))
        return

    out.data(f"Active runs: {len(runs)}")
    out.data("")

    for run_id, jobs_list in sorted(runs.items()):
        orchestrators = [j for j in jobs_list if j["role"] == "orchestrator"]
        workers = [j for j in jobs_list if j["role"] != "orchestrator"]

        # State summary.
        states: dict[str, int] = {}
        for j in jobs_list:
            s = j["state"]
            states[s] = states.get(s, 0) + 1
        state_str = ", ".join(f"{s}: {c}" for s, c in sorted(states.items()))

        orch_state = orchestrators[0]["state"] if orchestrators else "no-orchestrator"
        out.data(f"  {run_id}")
        out.data(f"    orchestrator: {orch_state}, workers: {len(workers)} ({state_str})")
        out.data("")


# ── self-install ─────────────────────────────────────────────────


@gcp_app.command("self-install")
def gcp_self_install(
    host: str = typer.Argument(..., help="SSH target (e.g. my-vm, user@hostname)"),
    zone: str = typer.Option("us-central1-b", "--zone", help="GCP zone"),
    project: str = typer.Option("", "--project", help="GCP project (default: from env)"),
    force: bool = typer.Option(False, "--force", help="Reinstall even if already present"),
) -> None:
    """Install metaproc on a remote GCP VM via SSH.

    Builds a wheel from the local metaproc source, pipes it to the remote host
    over SSH, and installs it with ``uv tool install``. After this, ``metaproc``
    is on PATH on the remote host.

    Skips installation if metaproc is already available. Use --force to
    reinstall with the current local version.

    Requires ``uv`` on the remote host.
    """
    effective_project = project or MetaprocEnv.METAPROC_GCP_PROJECT.read_str(default="")

    try:
        _run_self_install(host, zone=zone, project=effective_project, force=force)
    except RuntimeError as exc:
        raise CLIError(str(exc)) from exc


def _run_self_install(
    host: str,
    *,
    zone: str,
    project: str,
    force: bool = False,
) -> None:
    """Programmatic entry point for self-install (used by browse command).

    Checks if metaproc is already on PATH on the remote host. If present and
    force is False, skips installation. If force is True, always reinstalls.
    """

    from metaproc.cloud.gcp.dispatch_artifacts import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
        build_wheel,
    )

    # Prefix to ensure uv and uv-managed tools are on PATH for non-interactive SSH.
    path_prefix = (
        'export PATH="$HOME/.local/bin:$HOME/.local/share/uv/tools/metaproc/bin:$PATH" && '
    )

    # Check if metaproc is already available on the remote.
    if not force:
        check_cmd = build_ssh_command(
            host,
            remote_cmd=path_prefix + "metaproc --help > /dev/null 2>&1",
            gcp=True,
            zone=zone,
            project=project,
        )
        result = subprocess.run(check_cmd, capture_output=True)
        if result.returncode == 0:
            typer.echo(f"metaproc is already installed on {host}.")
            return

    # Build wheel.
    typer.echo("Building metaproc wheel...")
    wheel_path = build_wheel()
    typer.echo(f"Built: {wheel_path.name}")

    # Pipe wheel to remote and install. Preserve the original filename
    # because uv tool install parses version info from the wheel name.
    remote_wheel = f"/tmp/{wheel_path.name}"
    install_cmd = (
        path_prefix
        + f"cat > {remote_wheel}"
        + f" && uv tool install --force {remote_wheel}"
        + f" && rm {remote_wheel}"
    )

    ssh_cmd = build_ssh_command(
        host,
        remote_cmd=install_cmd,
        gcp=True,
        zone=zone,
        project=project,
    )

    typer.echo(f"Installing metaproc on {host}...")
    with open(wheel_path, "rb") as whl:
        result = subprocess.run(ssh_cmd, stdin=whl, capture_output=True, text=True)

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "uv: command not found" in stderr or "uv: not found" in stderr:
            raise RuntimeError(
                f"uv is not installed on {host}.\n\n"
                "Install uv on the remote host first:\n"
                f"  gcloud compute ssh {host} --zone {zone}"
                ' --command "curl -LsSf https://astral.sh/uv/install.sh | sh"'
            )
        raise RuntimeError(f"Installation failed on {host}:\n{stderr}")

    typer.echo(f"metaproc installed on {host}.")


# ── Asset types queried by `resources` ─────────────────────────
_ASSET_TYPES = [
    "batch.googleapis.com/Job",
    "file.googleapis.com/Instance",
    "storage.googleapis.com/Bucket",
    "artifactregistry.googleapis.com/Repository",
    "secretmanager.googleapis.com/Secret",
    "compute.googleapis.com/Network",
]

# Human-friendly labels keyed by asset type suffix.
_ASSET_TYPE_LABELS: dict[str, str] = {
    "Job": "Batch Jobs",
    "Instance": "Filestore Instances",
    "Bucket": "GCS Buckets",
    "Repository": "Artifact Registry Repos",
    "Secret": "Secret Manager Secrets",
    "Network": "VPC Networks",
}


def _require_gcp_asset() -> None:
    """Raise a clear error if google-cloud-asset is not installed."""
    try:
        from google.cloud import (  # noqa: PLC0415, F401 -- optional [gcp-batch] dependency
            asset_v1,
        )
    except ImportError:
        typer.echo(
            "GCP Asset Inventory support requires the [gcp-batch] extra.\n"
            "Install with: uv sync --extra gcp-batch",
            err=True,
        )
        raise typer.Exit(code=1) from None


def _search_resources(project: str) -> list[dict[str, object]]:
    """Query Cloud Asset Inventory for metaproc-related resources."""
    from google.cloud import asset_v1  # noqa: PLC0415 -- optional [gcp-batch] dependency

    client = asset_v1.AssetServiceClient()
    scope = f"projects/{project}"

    results: list[dict[str, object]] = []
    for asset_type in _ASSET_TYPES:
        request = asset_v1.SearchAllResourcesRequest(
            scope=scope,
            asset_types=[asset_type],
            query="name:metaproc",
            page_size=500,
        )
        try:
            for resource in client.search_all_resources(request=request):
                name = resource.name or ""
                # Extract short name from full resource path.
                short_name = name.split("/")[-1] if "/" in name else name
                results.append(
                    {
                        "asset_type": asset_type,
                        "name": short_name,
                        "full_name": name,
                        "location": resource.location or "",
                        "state": resource.state or "",
                        "labels": dict(resource.labels) if resource.labels else {},
                        "update_time": (
                            resource.update_time.isoformat()  # pyright: ignore[reportAttributeAccessIssue]
                            if resource.update_time
                            else ""
                        ),
                    }
                )
        except Exception as exc:
            results.append(
                {
                    "asset_type": asset_type,
                    "name": f"<error querying {asset_type}: {exc}>",
                    "full_name": "",
                    "location": "",
                    "state": "",
                    "labels": {},
                    "update_time": "",
                }
            )

    return results


@gcp_app.command("resources")
def gcp_resources(
    project: str = typer.Option("", "--project", help="GCP project (default: from env)"),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Cloud Asset Inventory snapshot of all metaproc-related resources.

    Shows permanent infra and versioned artifacts grouped by type.
    Note: inventory is eventually consistent — not for live run status.
    Use ``metaproc gcp runs`` or ``metaproc gcp status`` for live state.
    """
    _require_gcp_asset()

    out = get_output()

    effective_project = project or MetaprocEnv.METAPROC_GCP_PROJECT.read_str(default="")
    if not effective_project:
        raise CLIError("--project or METAPROC_GCP_PROJECT is required")

    try:
        resources = _search_resources(effective_project)
    except Exception as exc:
        raise CLIError(f"Failed to query Cloud Asset Inventory: {exc}") from exc

    if as_json:
        out.data(json.dumps(resources, indent=2))
        return

    if not resources:
        out.progress("No metaproc resources found.")
        raise typer.Exit(code=0)

    # Group by asset type.
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for r in resources:
        grouped[str(r["asset_type"])].append(r)

    out.data(f"Project: {effective_project}")
    out.data(f"Resources: {len(resources)}")
    out.data("")

    for asset_type in _ASSET_TYPES:
        items = grouped.get(asset_type, [])
        if not items:
            continue
        type_suffix = asset_type.split("/")[-1]
        label = _ASSET_TYPE_LABELS.get(type_suffix, type_suffix)
        out.data(f"  {label} ({len(items)})")
        for item in items:
            name = item["name"]
            location = item["location"]
            state = item["state"]
            parts = [str(name)]
            if location:
                parts.append(str(location))
            if state:
                parts.append(str(state))
            out.data(f"    {' | '.join(parts)}")
        out.data("")


# ── Filestore command ──────────────────────────────────────────


def _require_gcp_filestore() -> None:
    """Raise a clear error if google-cloud-filestore or monitoring is not installed."""
    try:
        from google.cloud import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
            filestore_v1,  # noqa: F401
            monitoring_v3,  # noqa: F401
        )
    except ImportError:
        typer.echo(
            "Filestore support requires the [gcp-batch] extra.\n"
            "Install with: uv sync --extra gcp-batch",
            err=True,
        )
        raise typer.Exit(code=1) from None


def _list_filestore_instances(project: str, region: str) -> list[dict[str, object]]:
    """List Filestore instances filtered to metaproc-related ones."""
    from google.cloud import filestore_v1  # noqa: PLC0415 -- optional [gcp-batch] dependency

    client = filestore_v1.CloudFilestoreManagerClient()
    parent = f"projects/{project}/locations/{region}"

    instances: list[dict[str, object]] = []
    for instance in client.list_instances(filestore_v1.ListInstancesRequest(parent=parent)):
        name = instance.name or ""
        short_name = name.split("/")[-1] if "/" in name else name

        # Only include metaproc-related instances.
        if "metaproc" not in short_name.lower():
            continue

        # Extract file share info.
        file_shares = list(instance.file_shares) if instance.file_shares else []
        share_name = file_shares[0].name if file_shares else ""
        capacity_gb = file_shares[0].capacity_gb if file_shares else 0

        # Extract network info for IP.
        networks = list(instance.networks) if instance.networks else []
        ip_addresses = (
            list(networks[0].ip_addresses) if networks and networks[0].ip_addresses else []
        )
        ip = ip_addresses[0] if ip_addresses else ""

        instances.append(
            {
                "name": short_name,
                "full_name": name,
                "state": filestore_v1.Instance.State(instance.state).name,
                "tier": filestore_v1.Instance.Tier(instance.tier).name,
                "capacity_gb": capacity_gb,
                "share_name": share_name,
                "ip": ip,
            }
        )

    return instances


def _query_filestore_utilization(project: str, instance_name: str) -> float | None:
    """Query Cloud Monitoring for filestore used_bytes_percent.

    Returns percentage (0-100) or None if unavailable.
    """

    from google.cloud import monitoring_v3  # noqa: PLC0415 -- optional [gcp-batch] dependency
    from google.protobuf import timestamp_pb2  # noqa: PLC0415 -- optional [gcp-batch] dependency

    client = monitoring_v3.MetricServiceClient()
    project_name = f"projects/{project}"

    now = datetime.now(UTC)
    end_time = timestamp_pb2.Timestamp()
    end_time.FromDatetime(now)
    start_time = timestamp_pb2.Timestamp()
    start_time.FromDatetime(now - timedelta(minutes=10))

    interval = monitoring_v3.TimeInterval(
        start_time=start_time,
        end_time=end_time,
    )

    # Extract short name from full resource path.
    short_name = instance_name.split("/")[-1] if "/" in instance_name else instance_name

    filter_str = (
        'metric.type = "file.googleapis.com/nfs/server/used_bytes_percent"'
        f' AND resource.labels.instance_name = "{short_name}"'
    )

    try:
        results = client.list_time_series(
            monitoring_v3.ListTimeSeriesRequest(
                name=project_name,
                filter=filter_str,
                interval=interval,
                view=monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
            )
        )
        for ts in results:
            points = list(ts.points)
            if points:
                return float(points[0].value.double_value)
    except Exception:
        log.warning("Failed to query filestore utilization for %s", instance_name, exc_info=True)

    return None


@gcp_app.command("filestore")
def gcp_filestore(
    project: str = typer.Option("", "--project", help="GCP project (default: from env)"),
    region: str = typer.Option("us-central1", "--region", help="GCP region"),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Filestore instance status and utilization.

    Shows instance details (name, state, tier, capacity, IP) and
    current disk utilization from Cloud Monitoring.
    """
    _require_gcp_filestore()

    out = get_output()

    effective_project = project or MetaprocEnv.METAPROC_GCP_PROJECT.read_str(default="")
    if not effective_project:
        raise CLIError("--project or METAPROC_GCP_PROJECT is required")

    try:
        instances = _list_filestore_instances(effective_project, region)
    except Exception as exc:
        raise CLIError(f"Failed to list Filestore instances: {exc}") from exc

    if not instances:
        out.progress("No metaproc Filestore instances found.")
        raise typer.Exit(code=0)

    # Enrich with utilization.
    for inst in instances:
        used_pct = _query_filestore_utilization(effective_project, str(inst["full_name"]))
        inst["used_pct"] = used_pct

    if as_json:
        out.data(json.dumps(instances, indent=2))
        return

    out.data(f"Project: {effective_project}")
    out.data(f"Filestore Instances: {len(instances)}")
    out.data("")

    for inst in instances:
        out.data(f"  {inst['name']}")
        out.data(f"    State:    {inst['state']}")
        out.data(f"    Tier:     {inst['tier']}")
        out.data(f"    Capacity: {inst['capacity_gb']} GB")
        used_pct = inst.get("used_pct")
        if used_pct is not None:
            out.data(f"    Used:     {used_pct:.1f}%")
        else:
            out.data("    Used:     (metrics unavailable)")
        out.data(f"    IP:       {inst['ip']}")
        out.data(f"    Share:    /{inst['share_name']}")
        out.data("")


# ── Archive ─────────────────────────────────────────────────────


def _require_gcp_storage() -> None:
    """Ensure google-cloud-storage is available."""
    try:
        from google.cloud import (  # noqa: PLC0415, F401 -- optional [gcp-batch] dependency
            storage,
        )
    except ImportError:
        typer.echo("google-cloud-storage is required: uv sync --extra gcp-batch", err=True)
        raise typer.Exit(code=1) from None


@gcp_app.command("archive")
def gcp_archive(
    run_dir: Path = typer.Argument(..., help="Local run directory to archive"),
    bucket: str = typer.Option("", "--bucket", help="GCS bucket name (or set METAPROC_GCS_BUCKET)"),
    delete_local: bool = typer.Option(False, "--delete-local", help="Delete local dir after sync"),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt"),
) -> None:
    """Sync a run directory to GCS for long-term storage.

    Mirrors the full run-tree (outputs, .state/, .logs/) to
    gs://{bucket}/archives/{run-dir-name}/.

    Use --delete-local (requires --yes) to remove the local copy after sync.
    """
    _require_gcp_storage()

    resolved = run_dir.resolve()
    if not resolved.is_dir():
        typer.echo(f"Not a directory: {resolved}", err=True)
        raise typer.Exit(code=1)

    effective_bucket = bucket or MetaprocEnv.METAPROC_GCS_BUCKET.read_str(default="")
    if not effective_bucket:
        raise CLIError("--bucket required or METAPROC_GCS_BUCKET must be set")
    run_name = resolved.name
    dest = f"gs://{effective_bucket}/archives/{run_name}/"

    out = get_output()
    out.data(f"Archive: {resolved}")
    out.data(f"Destination: {dest}")

    # Count files for progress feedback.
    file_count = sum(1 for _ in resolved.rglob("*") if _.is_file())
    out.data(f"Files: {file_count}")

    if not yes:
        typer.confirm("Proceed with archive?", abort=True)

    # Use gsutil for reliable parallel rsync.
    cmd = ["gsutil", "-m", "rsync", "-r", str(resolved), dest]
    out.data(f"Running: {' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        typer.echo(f"gsutil rsync failed (rc={result.returncode}):\n{result.stderr}", err=True)
        raise typer.Exit(code=result.returncode)

    out.data(f"Archived {file_count} files to {dest}")

    if delete_local:
        if not yes:
            typer.confirm(f"Delete local directory {resolved}?", abort=True)
        shutil.rmtree(resolved)
        out.data(f"Deleted local directory: {resolved}")


# ── Remote repo bootstrap ─────────────────────────────────────


_REMOTE_REPO_DIR = "~/metaproc-repo"


def _remote_editable_install_command() -> str:
    """Build the remote editable-install command from core and plugin package paths."""
    plugin_defaults = get_bootstrap_env_vars()
    core_path = MetaprocEnv.METAPROC_REPO_PACKAGE_PATH.read_str(
        default=plugin_defaults.get("METAPROC_REPO_PACKAGE_PATH", ".")
    ).strip()
    companion_csv = MetaprocEnv.METAPROC_WORKSPACE_PACKAGES.read_str(
        default=plugin_defaults.get("METAPROC_WORKSPACE_PACKAGES", "")
    )
    package_paths = [core_path or "."]
    package_paths.extend(path.strip() for path in companion_csv.split(",") if path.strip())
    editable_args = " ".join(f"-e {shlex.quote(path)}" for path in package_paths)
    return f"uv pip install {editable_args}"


def _ensure_remote_repo(
    host: str,
    *,
    zone: str,
    project: str,
    sync: bool = False,
) -> None:
    """Ensure the full repo is cloned on a remote host.

    On first run, clones ``METAPROC_REPO_URL`` / ``METAPROC_RUN_BRANCH`` into
    ``~/metaproc-repo`` and installs Metaproc plus any plugin-configured companion
    packages in editable mode. Subsequent calls with ``sync=False`` (the default) skip if the repo
    already exists.  ``sync=True`` does a ``git pull`` + reinstall.

    Mirrors the bootstrap logic in ``container_bootstrap.py`` but runs over SSH
    on a long-lived VM (browser host), not inside a disposable container.
    """
    repo_url = MetaprocEnv.METAPROC_REPO_URL.read_str(default="")
    run_branch = MetaprocEnv.METAPROC_RUN_BRANCH.read_str(default="main")
    install_command = _remote_editable_install_command()

    if not repo_url:
        typer.echo(
            "METAPROC_REPO_URL is required for remote repo bootstrap. "
            "Set it in your shell profile or .env.",
            err=True,
        )
        raise typer.Exit(code=1)

    out = get_output()

    # Check if repo already exists.
    check_cmd = build_ssh_command(
        host,
        remote_cmd=f"test -d {_REMOTE_REPO_DIR}/.git",
        gcp=True,
        zone=zone,
        project=project,
        iap=True,
    )
    repo_exists = subprocess.run(check_cmd, capture_output=True).returncode == 0

    if repo_exists and not sync:
        out.data(f"Remote repo already exists on {host} — skipping (use --sync to update)")
        return

    if repo_exists and sync:
        out.data(f"Updating remote repo on {host} (branch: {run_branch})")
        update_script = (
            f"cd {_REMOTE_REPO_DIR}"
            f" && git fetch origin"
            f" && git checkout {shlex.quote(run_branch)}"
            f" && git reset --hard origin/{shlex.quote(run_branch)}"
            f" && {install_command}"
        )
        ssh_cmd = build_ssh_command(
            host,
            remote_cmd=update_script,
            gcp=True,
            zone=zone,
            project=project,
            iap=True,
        )
        result = subprocess.run(ssh_cmd)
        if result.returncode != 0:
            typer.echo(f"Failed to update remote repo on {host}", err=True)
            raise typer.Exit(code=result.returncode)
        out.data(f"Remote repo updated on {host}")
        return

    # Fresh clone.
    out.data(f"Cloning repo on {host} (branch: {run_branch})")
    clone_script = (
        f"git clone --depth 1 --branch {shlex.quote(run_branch)}"
        f" {shlex.quote(repo_url)} {_REMOTE_REPO_DIR}"
        f" && cd {_REMOTE_REPO_DIR}"
        f" && {install_command}"
    )
    ssh_cmd = build_ssh_command(
        host,
        remote_cmd=clone_script,
        gcp=True,
        zone=zone,
        project=project,
        iap=True,
    )
    result = subprocess.run(ssh_cmd)
    if result.returncode != 0:
        typer.echo(f"Failed to clone repo on {host}", err=True)
        raise typer.Exit(code=result.returncode)
    out.data(f"Remote repo bootstrapped on {host}")


# ── Remote command execution ──────────────────────────────────────


@gcp_app.command("remote")
def gcp_remote(
    command: list[str] = typer.Argument(
        ..., help="metaproc command and arguments to run on the remote host"
    ),
    host: str = typer.Option(
        "",
        "--host",
        help="GCE gateway host (default: $METAPROC_GATEWAY_HOST or metaproc-browser)",
    ),
    zone: str = typer.Option("us-central1-b", "--zone", help="GCP zone"),
    project: str = typer.Option("", "--project", help="GCP project (default: from env)"),
    run_id: str = typer.Option(
        "",
        "--run-id",
        help="Run id — expands to the Filestore run directory on the remote host",
    ),
    phase: str = typer.Option(
        "mine",
        "--phase",
        help="Phase subdirectory within the run (default: mine)",
    ),
    sync: bool = typer.Option(
        False,
        "--sync",
        help="Pull latest code and reinstall on the remote host before running",
    ),
    raw: bool = typer.Option(
        False,
        "--raw",
        help="Run command as-is without prepending 'metaproc'",
    ),
) -> None:
    """Run a metaproc command on the remote gateway host via SSH.

    Bootstraps the repo on the remote host if needed, then executes the given
    metaproc command over SSH with IAP tunneling.

    If ``--run-id`` is provided, the run directory path is expanded and passed
    as the last positional argument to the remote command::

        metaproc gcp remote stats --run-id cloud-500-ds-2026-04-12
        # → ssh: metaproc stats /mnt/filestore/runs/cloud-500-ds-2026-04-12/mine

    With ``--raw``, the command is passed through without the ``metaproc``
    prefix, allowing arbitrary commands to run on the remote host::

        metaproc gcp remote --raw python -m companion_package.report ...

    Without ``--run-id``, the command is passed through as-is.
    """
    effective_host = _resolve_gateway_host(host)
    effective_project = project or MetaprocEnv.METAPROC_GCP_PROJECT.read_str(default="")
    if not effective_project:
        typer.echo("--project required or set METAPROC_GCP_PROJECT", err=True)
        raise typer.Exit(code=1)

    # Raw commands like ``ls /mnt/filestore/...`` do not need the repo bootstrap.
    # Keep bootstrap for metaproc commands, and honor explicit --sync even in raw mode.
    if not raw or sync:
        _ensure_remote_repo(
            effective_host,
            zone=zone,
            project=effective_project,
            sync=sync,
        )

    # Build the remote command.
    remote_base = _default_filestore_remote_runs_dir()
    path_prefix = 'export PATH="$HOME/.local/bin:$PATH" && '
    cmd_parts = list(command)

    if run_id:
        run_dir = f"{remote_base}/{run_id}/{phase}"
        cmd_parts.append(run_dir)

    if raw:
        remote_cmd = path_prefix + " ".join(shlex.quote(p) for p in cmd_parts)
    else:
        remote_cmd = path_prefix + "metaproc " + " ".join(shlex.quote(p) for p in cmd_parts)

    out = get_output()
    label = " ".join(cmd_parts) if raw else f"metaproc {' '.join(cmd_parts)}"
    out.data(f"Running on {effective_host}: {label}")

    ssh_cmd = build_ssh_command(
        effective_host,
        remote_cmd=remote_cmd,
        gcp=True,
        zone=zone,
        project=effective_project,
        iap=True,
    )
    result = subprocess.run(ssh_cmd)
    if result.returncode != 0:
        raise typer.Exit(code=result.returncode)


# ── Remote-run: tmux session launch ─────────────────────────────


_REMOTE_WORKTREE_BASE = "~/metaproc-worktrees"


def _check_clean_branch() -> tuple[str, str]:
    """Verify working tree is clean and current branch is pushed.

    Returns (branch_name, commit_sha). Raises typer.Exit on failure.
    """
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        typer.echo(
            "Working tree has uncommitted changes. Commit and push before remote-run.",
            err=True,
        )
        raise typer.Exit(code=1)

    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    ).stdout.strip()

    remote_sha = subprocess.run(
        ["git", "rev-parse", f"origin/{branch}"],
        capture_output=True,
        text=True,
    )
    if remote_sha.returncode != 0 or remote_sha.stdout.strip() != sha:
        typer.echo(
            f"Branch '{branch}' is not pushed to origin (or origin/{branch} "
            f"is behind). Push first: git push -u origin {branch}",
            err=True,
        )
        raise typer.Exit(code=1)

    return branch, sha


# Env vars propagated from local shell to the remote tmux session.
# ADC is handled by the VM's attached service account — no key files needed.
_REMOTE_RUN_ENV_VARS = [
    "METAPROC_GCP_PROJECT",
    "METAPROC_GCP_CONTAINER_IMAGE",
    "METAPROC_GCP_SERVICE_ACCOUNT",
    "METAPROC_GCP_FILESTORE_SERVER",
    "METAPROC_GCP_FILESTORE_MOUNT_PATH",
    "METAPROC_GCP_SECRET_GH_TOKEN",
    "METAPROC_REPO_URL",
    "METAPROC_RUN_BRANCH",
    "METAPROC_REPO_PACKAGE_PATH",
    "METAPROC_WORKSPACE_PACKAGES",
    "RUNS_DIR",
]


def _build_env_exports() -> str:
    """Build export statements for env vars to propagate to the remote session."""
    exports = []
    plugin_defaults = get_bootstrap_env_vars()
    for var_name in _REMOTE_RUN_ENV_VARS:
        val = os.environ.get(var_name, plugin_defaults.get(var_name, ""))
        if val:
            exports.append(f"export {var_name}={shlex.quote(val)}")
    return " && ".join(exports) if exports else "true"


def _build_worktree_script(
    session_name: str,
    branch: str,
    sha: str,
    metaproc_cmd: str,
) -> str:
    """Build the remote shell script that creates a worktree and launches tmux."""
    worktree_dir = f"{_REMOTE_WORKTREE_BASE}/{session_name}"
    env_exports = _build_env_exports()
    install_command = _remote_editable_install_command()
    tmux_inner = (
        f"cd {shlex.quote(worktree_dir)}"
        f" && export PATH=$HOME/.local/bin:$PATH"
        f" && {env_exports}"
        f" && {metaproc_cmd}"
    )
    return (
        f"test -d {_REMOTE_REPO_DIR}/.git"
        f" || {{ echo 'Base repo {_REMOTE_REPO_DIR} not found — run gcp remote first'; exit 1; }}"
        f" && cd {_REMOTE_REPO_DIR}"
        f" && git fetch origin {shlex.quote(branch)}"
        f" && git worktree add {shlex.quote(worktree_dir)} {shlex.quote(sha)}"
        f" && cd {shlex.quote(worktree_dir)}"
        f' && export PATH="$HOME/.local/bin:$PATH"'
        f" && {install_command}"
        f" && tmux new-session -d -s {shlex.quote(session_name)}"
        f' "{tmux_inner}"'
    )


@gcp_app.command("remote-run")
def gcp_remote_run(
    process_spec: str = typer.Argument(
        "",
        help=(
            "Path to .process.md spec file (relative to repo root, "
            "e.g. examples/hello/hello.process.md)"
        ),
    ),
    var: list[str] = typer.Option(  # noqa: B006
        [], "--var", help="Variable KEY=VALUE (repeatable)"
    ),
    backend: str = typer.Option("gcp-worker", "--backend", help="Execution backend"),
    variant: str = typer.Option("", "--variant", help="Model variant"),
    host: str = typer.Option(
        "",
        "--host",
        help="GCE gateway host (default: $METAPROC_GATEWAY_HOST)",
    ),
    zone: str = typer.Option("us-central1-b", "--zone", help="GCP zone"),
    project: str = typer.Option("", "--project", help="GCP project (default: from env)"),
    session_name: str = typer.Option(
        "",
        "--session-name",
        help="tmux session name (default: derived from run-id or process name)",
    ),
    list_sessions: bool = typer.Option(
        False, "--list", help="List active tmux sessions on the remote host"
    ),
    attach: bool = typer.Option(False, "--attach", help="Attach to an existing tmux session"),
    kill: bool = typer.Option(False, "--kill", help="Kill a tmux session"),
    max_concurrency: int = typer.Option(
        0, "--max-concurrency", help="Max concurrency (0 = use default)"
    ),
    num_workers: int = typer.Option(0, "--num-workers", help="Number of workers (0 = use default)"),
    max_duration: str = typer.Option("8h", "--max-duration", help="Max duration for the run"),
    force: bool = typer.Option(False, "--force", help="Force re-run even if completed"),
    spot: bool = typer.Option(
        True, "--spot/--no-spot", help="Use Spot VMs for gcp-worker backend (default: on)"
    ),
    continue_on_error: bool = typer.Option(
        False,
        "--continue-on-error",
        help="Continue executing remaining items if one fails",
    ),
    cleanup: bool = typer.Option(
        False, "--cleanup", help="Remove stale worktrees (no active tmux session)"
    ),
) -> None:
    """Launch metaproc run-process in a tmux session on a remote GCE host.

    Requires a clean, pushed branch. Creates a per-session git worktree on the
    remote host pinned to the current commit, then starts a tmux session running
    ``metaproc run-process``. The session survives SSH disconnects.

    Use ``--list`` to see active sessions, ``--attach`` to reconnect,
    ``--kill`` to terminate a session, or ``--cleanup`` to remove stale worktrees.
    """
    effective_host = _resolve_gateway_host(host)
    effective_project = project or MetaprocEnv.METAPROC_GCP_PROJECT.read_str(default="")
    if not effective_project:
        typer.echo("--project required or set METAPROC_GCP_PROJECT", err=True)
        raise typer.Exit(code=1)

    out = get_output()

    # ── --list mode ──────────────────────────────────────────────
    if list_sessions:
        ssh_cmd = build_ssh_command(
            effective_host,
            remote_cmd="tmux list-sessions 2>/dev/null || echo '(no active tmux sessions)'",
            gcp=True,
            zone=zone,
            project=effective_project,
            iap=True,
        )
        subprocess.run(ssh_cmd)
        return

    # ── --attach mode ────────────────────────────────────────────
    if attach:
        if not session_name:
            typer.echo("--session-name required with --attach", err=True)
            raise typer.Exit(code=1)
        ssh_cmd = build_ssh_command(
            effective_host,
            remote_cmd=f"tmux attach-session -t {shlex.quote(session_name)}",
            gcp=True,
            zone=zone,
            project=effective_project,
            iap=True,
            interactive=True,
        )
        result = subprocess.run(ssh_cmd)
        if result.returncode != 0:
            raise typer.Exit(code=result.returncode)
        return

    # ── --kill mode ──────────────────────────────────────────────
    if kill:
        if not session_name:
            typer.echo("--session-name required with --kill", err=True)
            raise typer.Exit(code=1)
        ssh_cmd = build_ssh_command(
            effective_host,
            remote_cmd=f"tmux kill-session -t {shlex.quote(session_name)}",
            gcp=True,
            zone=zone,
            project=effective_project,
            iap=True,
        )
        result = subprocess.run(ssh_cmd)
        if result.returncode == 0:
            out.data(f"Killed tmux session: {session_name}")
        else:
            typer.echo(f"Failed to kill session '{session_name}' (may not exist)", err=True)
            raise typer.Exit(code=result.returncode)
        return

    # ── --cleanup mode ──────────────────────────────────────────
    if cleanup:
        # Remove worktrees that have no matching active tmux session.
        cleanup_script = (
            f'export PATH="$HOME/.local/bin:$PATH"'
            f" && cd {_REMOTE_REPO_DIR} 2>/dev/null || exit 0"
            f" && active_sessions=$(tmux list-sessions -F '#{{session_name}}' 2>/dev/null || true)"
            f" && for wt in {_REMOTE_WORKTREE_BASE}/*/; do"
            f'   name=$(basename "$wt");'
            f'   if ! echo "$active_sessions" | grep -qx "$name"; then'
            f'     echo "Removing stale worktree: $name";'
            f'     git worktree remove --force "$wt" 2>/dev/null || rm -rf "$wt";'
            f"   else"
            f'     echo "Keeping active worktree: $name";'
            f"   fi;"
            f" done"
            f" && git worktree prune"
        )
        ssh_cmd = build_ssh_command(
            effective_host,
            remote_cmd=cleanup_script,
            gcp=True,
            zone=zone,
            project=effective_project,
            iap=True,
        )
        result = subprocess.run(ssh_cmd)
        if result.returncode != 0:
            typer.echo("Worktree cleanup failed", err=True)
            raise typer.Exit(code=result.returncode)
        out.data("Worktree cleanup complete")
        return

    # ── Launch mode ──────────────────────────────────────────────

    if not process_spec:
        typer.echo("process_spec argument required for launch mode", err=True)
        raise typer.Exit(code=1)

    # Remote preflight: verify tmux, uv, and git are available.
    preflight_cmd = (
        'which tmux >/dev/null 2>&1 || { echo "FAIL: tmux not found"; exit 1; }'
        " && which uv >/dev/null 2>&1 || which $HOME/.local/bin/uv >/dev/null 2>&1"
        ' || { echo "FAIL: uv not found"; exit 1; }'
        ' && which git >/dev/null 2>&1 || { echo "FAIL: git not found"; exit 1; }'
        ' && echo "OK: tmux, uv, git available"'
    )
    ssh_cmd = build_ssh_command(
        effective_host,
        remote_cmd=preflight_cmd,
        gcp=True,
        zone=zone,
        project=effective_project,
        iap=True,
    )
    preflight_result = subprocess.run(ssh_cmd, capture_output=True, text=True)
    if preflight_result.returncode != 0:
        typer.echo(
            f"Remote preflight failed on {effective_host}:\n{preflight_result.stdout}{preflight_result.stderr}",
            err=True,
        )
        raise typer.Exit(code=1)
    out.data(f"Remote preflight: {preflight_result.stdout.strip()}")

    # Clean-branch guard.
    branch, sha = _check_clean_branch()
    out.data(f"Branch: {branch} ({sha[:8]})")

    # Ensure base repo exists on the remote host.
    _ensure_remote_repo(
        effective_host,
        zone=zone,
        project=effective_project,
        sync=False,
    )

    # Derive session name if not provided.
    if not session_name:
        for v in var:
            if v.startswith("RUN_ID="):
                session_name = v.split("=", 1)[1]
                break
        if not session_name:
            # Derive session name from the spec filename with ``.process.md``
            # stripped (e.g. ``mine.process.md`` → ``mine``,
            # ``browse-filestore.process.md`` → ``browse-filestore``).
            spec_label = Path(process_spec).name.removesuffix(".process.md") or "run"
            session_name = f"{spec_label}-{sha[:8]}"

    # Build the remote metaproc command.
    cmd_parts = [
        "metaproc",
        "run-process",
        shlex.quote(process_spec),
        "--backend",
        shlex.quote(backend),
    ]
    for v in var:
        cmd_parts.extend(["--var", shlex.quote(v)])
    if variant:
        cmd_parts.extend(["--variant", shlex.quote(variant)])
    if max_concurrency > 0:
        cmd_parts.extend(["--max-concurrency", str(max_concurrency)])
    if num_workers > 0:
        cmd_parts.extend(["--num-workers", str(num_workers)])
    if max_duration:
        cmd_parts.extend(["--max-duration", shlex.quote(max_duration)])
    if not spot:
        cmd_parts.append("--no-spot")
    if continue_on_error:
        cmd_parts.append("--continue-on-error")
    if force:
        cmd_parts.append("--force")

    metaproc_cmd = " ".join(cmd_parts)

    # Build and execute the worktree + tmux launch script.
    launch_script = _build_worktree_script(session_name, branch, sha, metaproc_cmd)

    out.data(f"Launching tmux session '{session_name}' on {effective_host}")
    out.data(f"  Process: {process_spec}")
    out.data(f"  Backend: {backend}")
    if variant:
        out.data(f"  Variant: {variant}")
    out.data(f"  Commit: {sha[:8]}")

    ssh_cmd = build_ssh_command(
        effective_host,
        remote_cmd=launch_script,
        gcp=True,
        zone=zone,
        project=effective_project,
        iap=True,
    )
    result = subprocess.run(ssh_cmd)
    if result.returncode != 0:
        typer.echo("Failed to launch remote-run session", err=True)
        raise typer.Exit(code=result.returncode)

    out.data(f"\nSession '{session_name}' launched successfully.")
    out.data(f"  Attach: metaproc gcp remote-run --attach --session-name {session_name}")
    out.data("  List:   metaproc gcp remote-run --list")
    out.data(f"  Kill:   metaproc gcp remote-run --kill --session-name {session_name}")


# ── Sync run from Filestore ─────────────────────────────────────


def _default_filestore_remote_runs_dir() -> str:
    """Default remote runs dir on the gateway host.

    All VM types (workers, orchestrators, browser host) mount the Filestore
    share at /mnt/filestore.  Run trees live at /mnt/filestore/runs/{run_id}/.
    Override with --remote-runs-dir or $METAPROC_GCP_FILESTORE_REMOTE_RUNS_DIR
    if your gateway host mounts the share elsewhere.
    """
    return MetaprocEnv.METAPROC_GCP_FILESTORE_REMOTE_RUNS_DIR.read_str(
        default="/mnt/filestore/runs"
    )


def _resolve_gateway_host(host: str) -> str:
    """Resolve the gateway host from CLI arg or METAPROC_GATEWAY_HOST env var."""
    if host:
        return host
    env_host = MetaprocEnv.METAPROC_GATEWAY_HOST.read_str(default="")
    if env_host:
        return env_host
    raise CLIError("--host required or METAPROC_GATEWAY_HOST must be set")


def run_remote_metaproc(
    args: list[str],
    *,
    host: str = "",
    zone: str = "us-central1-b",
    project: str = "",
) -> str:
    """Run a metaproc command on the Filestore gateway VM via IAP SSH.

    Used by ``metaproc status --remote`` to obtain per-run state without a
    local SSHFS mount: the gateway VM already has Filestore mounted and
    metaproc installed. One-shot exec avoids SSHFS fragility entirely.

    Parameters
    ----------
    args
        Positional args for the remote ``metaproc`` invocation
        (e.g. ``["status", "/mnt/filestore/runs/<run-id>", "--format", "json"]``).
    host
        Gateway VM name; falls back to ``METAPROC_GATEWAY_HOST`` or
        ``metaproc-browser``.
    zone, project
        GCP zone and project; project falls back to ``METAPROC_GCP_PROJECT``.

    Returns
    -------
    str
        stdout from the remote command.

    Raises
    ------
    metaproc.errors.CLIError
        If no GCP project is resolvable, or the remote command fails.
    """

    effective_host = _resolve_gateway_host(host)
    effective_project = project or MetaprocEnv.METAPROC_GCP_PROJECT.read_str(default="")
    if not effective_project:
        raise CLIError("--project required or METAPROC_GCP_PROJECT must be set")

    path_prefix = 'export PATH="$HOME/.local/bin:$PATH" && '
    remote_cmd = path_prefix + "metaproc " + " ".join(shlex.quote(p) for p in args)

    ssh_cmd = build_ssh_command(
        effective_host,
        remote_cmd=remote_cmd,
        gcp=True,
        zone=zone,
        project=effective_project,
        iap=True,
    )
    result = subprocess.run(ssh_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise CLIError(
            f"Remote metaproc failed on {effective_host} (exit {result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout


# sync-run was removed in Phase 5 of the remote job process management spec.
# Use `gcp remote` to run inspection commands directly on the Filestore host
# instead of copying run trees locally.


# ── Cleanup ─────────────────────────────────────────────────────


@gcp_app.command("cleanup")
def gcp_cleanup(
    older_than: int = typer.Option(30, "--older-than", help="Delete jobs older than N days"),
    project: str = typer.Option("", "--project", help="GCP project (default: from env)"),
    region: str = typer.Option("us-central1", "--region", help="GCP region"),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt"),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Delete old GCP Batch jobs in terminal states.

    Lists Batch jobs in SUCCEEDED, FAILED, or CANCELLED state that are older
    than --older-than days, then deletes them after confirmation.

    GCP auto-deletes Batch jobs after 60 days. Use this for earlier cleanup.
    """
    _require_gcp_batch()
    effective_project = project or MetaprocEnv.METAPROC_GCP_PROJECT.read_str(default="")
    if not effective_project:
        typer.echo("--project required or set METAPROC_GCP_PROJECT", err=True)
        raise typer.Exit(code=1)

    from google.cloud import batch_v1  # noqa: PLC0415 -- optional [gcp-batch] dependency
    from google.cloud.batch_v1.types.job import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
        JobStatus,
    )

    out = get_output()
    client = batch_v1.BatchServiceClient()
    parent = f"projects/{effective_project}/locations/{region}"
    cutoff = datetime.now(tz=UTC) - timedelta(days=older_than)

    terminal_states = {
        JobStatus.State.SUCCEEDED,
        JobStatus.State.FAILED,
        # DELETION_IN_PROGRESS is also terminal but don't re-delete
    }

    # List all jobs and filter to old terminal ones.
    candidates: list[dict[str, str]] = []
    try:
        for job in client.list_jobs(batch_v1.ListJobsRequest(parent=parent)):
            if job.status.state not in terminal_states:
                continue
            create_time: Any = job.create_time
            if create_time and create_time < cutoff:
                job_name = job.name
                age_days = (datetime.now(tz=UTC) - create_time).days
                candidates.append(
                    {
                        "name": job_name,
                        "state": JobStatus.State(job.status.state).name,
                        "age_days": age_days,
                        "created": create_time.isoformat(),
                    }
                )
    except Exception:
        log.warning("Failed to list Batch jobs for %s", parent, exc_info=True)
        raise typer.Exit(code=1) from None

    if not candidates:
        out.data(f"No Batch jobs older than {older_than} days in terminal state.")
        return

    if as_json:
        out.data(json.dumps(candidates, indent=2))
        if not yes:
            typer.confirm(f"Delete {len(candidates)} jobs?", abort=True)
    else:
        out.data(f"Found {len(candidates)} Batch job(s) older than {older_than} days:")
        out.data("")
        for c in candidates:
            short_name = c["name"].rsplit("/", 1)[-1]
            out.data(f"  {short_name}  {c['state']}  {c['age_days']}d old")
        out.data("")

        if not yes:
            typer.confirm(f"Delete {len(candidates)} jobs?", abort=True)

    deleted = 0
    for c in candidates:
        try:
            client.delete_job(batch_v1.DeleteJobRequest(name=c["name"]))
            deleted += 1
        except Exception:
            log.warning("Failed to delete job %s", c["name"], exc_info=True)

    out.data(f"Deleted {deleted}/{len(candidates)} jobs.")
