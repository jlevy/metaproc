"""metaproc run-process — orchestrate a full process spec DAG.

Walks the step dependency graph in topological order, dispatching each step
to the correct execution path based on its mode. Phase 1 executes steps
sequentially (one at a time); Phase 2 will add ``asyncio.gather()`` for
independent steps at the same topological level.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import json as _json
import logging
import os
import re
import shlex
import subprocess
import time
import traceback
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import typer
from prettyfmt import fmt_timedelta
from ruamel.yaml import YAMLError
from strif import atomic_output_file

from metaproc import paths as paths_mod
from metaproc.adapters.registry import derive_variant, get_adapter
from metaproc.cli import app, get_output
from metaproc.cloud.gcp.resolve_token import resolve_gcp_token
from metaproc.cloud.gcp.worker_dispatch import (
    WorkerDispatchConfig,
    build_gcp_config_from_env,
    dispatch_to_workers,
    reconcile_dispatched_workers,
)
from metaproc.commands.helpers import (
    enforce_no_unresolved_placeholders,
    load_process_spec,
    parse_adapter_config,
    parse_step_variant_args,
    parse_var_args,
    require_runtime_runs_dir,
    resolve_process_path,
    resolve_record_output_paths,
    seed_runtime_vars,
)
from metaproc.config.env_vars import MetaprocEnv
from metaproc.dispatch.auth_pool_flags import AuthPoolFlags
from metaproc.dispatch.credential_pool import (
    FallbackPolicy,
    SelectionPolicy,
    SelectionStrategy,
    gcp_backend,
    local_backend,
)
from metaproc.dispatch.pool_dispatch import PoolDispatchConfig
from metaproc.dispatch.preflight import (
    GuardPosture,
    check_dispatch_auth_env,
    validate_guard_posture,
)
from metaproc.dispatch.slot_coordinator import SlotCoordinator
from metaproc.engine.build_plan import build_plan, merge_defaults
from metaproc.engine.code_handler import resolve_code_handler
from metaproc.engine.dep_state import (
    fingerprint_step,
    recorded_step_hash,
)
from metaproc.engine.discovery import discover_items_from_source
from metaproc.engine.fan_in import write_outcome_manifest
from metaproc.engine.graph import (
    downstream,
    item_aligned_chains,
    propagate_failure,
    topo_sort,
)
from metaproc.engine.input_validation import validate_process_inputs, validate_process_outputs
from metaproc.engine.item_runner import (
    StepInvoker,
    run_aligned_chain,
    run_fan_out,
    with_retry,
)
from metaproc.engine.pathing import (
    compute_logs_dir,
    compute_run_dir,
    compute_task_logs_dir,
    compute_task_state_dir,
)
from metaproc.engine.placeholders import (
    collect_step_runtime_placeholders,
    resolve_runtime_config,
    resolve_templates,
    validate_spec_placeholders,
)
from metaproc.engine.preflight import run_preflight
from metaproc.engine.process_scope import expand_process_vars
from metaproc.engine.resource_finalization import finalize_run_resources
from metaproc.engine.resource_sampling import run_sampled_step_command, sample_step_resources
from metaproc.engine.resource_snapshot import build_resource_run_snapshot
from metaproc.engine.retry import (
    FailureClass,
    RetryVerdict,
    append_output_failure_feedback,
    classify_error,
    classify_failure,
    classify_output_failures,
    compute_backoff,
    extract_log_error,
    max_retries_for,
)
from metaproc.engine.runtime import prepare_step, resolve_batch_size, validate_step_inputs_exist
from metaproc.engine.schema_conform import conform_declared_outputs
from metaproc.engine.validation import (
    repair_declared_outputs,
    validate_item_outputs,
    validate_item_outputs_detailed,
)
from metaproc.engine.write_boundary import (
    WriteTarget,
    capture_repo_snapshot,
    collect_write_targets,
    filter_boundary_violations,
    repo_changes_since,
)
from metaproc.errors import CLIError, ValidationError
from metaproc.io import read_yaml_file, to_yaml_string
from metaproc.io.orchestrator_lease import LeaseHeartbeat, acquire_lease, release_lease
from metaproc.io.overrides import (
    OverridesDocument,
    is_step_overridden,
    is_step_satisfied_universally,
    overrides_for_step,
    read_overrides,
)
from metaproc.io.state_io import (
    compute_item_dir,
    mark_completed_at,
    mark_failed_at,
    mark_running_at,
    read_manual_ack_at,
    read_status_at,
    reconcile_stale_running,
    write_attempt_at,
    write_result_at,
)
from metaproc.logutil.compaction import try_compact_log
from metaproc.models.authored import IOSpec, ProcessSpec, ProcessStep
from metaproc.models.plan import Plan, ResolvedStep
from metaproc.models.resource_budget import FinalizationState
from metaproc.models.resource_snapshot import ResourceRunSnapshot
from metaproc.models.runtime import AttemptRecord, OutputFailure, ResultRecord, StatusRecord
from metaproc.paths import (
    LOGS_DIR,
    MANUAL_ACK_FILE,
    RUN_LAYOUT_VERSION,
    STATE_DIR,
    STATUS_FILE,
)
from metaproc.paths import STATE_DIR as _STATE_DIR
from metaproc.paths import TASKS_SUBDIR as _TASKS_SUBDIR
from metaproc.runpool.kill import install_subprocess_reaper_signal_handlers
from metaproc.runpool.pool import (
    RunPoolConfig,
    resolve_estimated_process_rss_bytes,
    resolve_host_max_concurrency,
    resolve_initial_memory_budget_fraction,
)
from metaproc.runpool.process_events import ProcessEventLogger
from metaproc.runpool.registry import get_backend
from metaproc.runpool.scalar_admission import SCALAR_DEFAULT_HOST_LIMIT, admitted_launch
from metaproc.viz_loader import load_plan_bundle

log = logging.getLogger(__name__)

MANUAL_ACK_TIMEOUT_S = 24 * 3600
MANUAL_ACK_HEARTBEAT_S = 15 * 60


# ── Helpers ──────────────────────────────────────────────────────


def _parse_duration(value: str) -> int:
    """Parse a duration string like '8h', '3600s', or '2h30m' into seconds."""

    if value.isdigit():
        return int(value)

    total = 0
    for match in re.finditer(r"(\d+)\s*(h|m|s)", value, re.IGNORECASE):
        num = int(match.group(1))
        unit = match.group(2).lower()
        if unit == "h":
            total += num * 3600
        elif unit == "m":
            total += num * 60
        elif unit == "s":
            total += num
    if total == 0:
        raise CLIError(f"Invalid duration: '{value}'. Use formats like '8h', '3600s', or '2h30m'.")
    return total


def _format_boundary_paths(
    violations: list[Path],
    *,
    base_dir: Path | None,
) -> str:
    formatted: list[str] = []
    for violation in violations:
        if base_dir is not None:
            try:
                formatted.append(str(violation.relative_to(base_dir)))
                continue
            except ValueError:
                pass
        formatted.append(str(violation))
    return ", ".join(formatted)


def _dedupe_write_targets(targets: list[WriteTarget]) -> list[WriteTarget]:
    deduped: list[WriteTarget] = []
    seen: set[tuple[Path, str]] = set()
    for target in targets:
        key = (target.path, target.kind)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(target)
    return deduped


def _step_concurrency_targets(
    *,
    target: ResolvedStep,
    step_def: ProcessStep,
    variables: dict[str, str],
    run_dir: Path,
) -> list[WriteTarget]:
    """Approximate a step's write surface for peer-boundary filtering.

    These peer targets are intentionally broader than a step's own strict
    boundary so concurrent sibling steps in the same topological level do not
    false-trigger each other's repo snapshots.
    """
    targets = collect_write_targets(
        step_def,
        target.outputs,
        variables,
        step_scope=target.fan_out is not None,
    )
    targets.append(
        WriteTarget(
            _resolve_step_item_dir(run_dir, target.step_id, target.outputs, variables),
            "tree",
        )
    )
    return _dedupe_write_targets(targets)


# ── Process-level status model ────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S")


StepState = str  # "pending" | "running" | "completed" | "failed" | "skipped" | "blocked"


def _write_process_status(
    run_dir: Path,
    process_name: str,
    step_states: dict[str, dict[str, Any]],
    started_at: str,
) -> Path:
    """Write derived process-status.yaml to {run_dir}/.state/."""
    state_dir = run_dir / STATE_DIR
    state_dir.mkdir(parents=True, exist_ok=True)
    target = state_dir / "process-status.yaml"

    data: dict[str, Any] = {
        "process": process_name,
        "started_at": started_at,
        "steps": step_states,
    }

    # Determine overall state
    states = {s.get("state", "pending") for s in step_states.values()}
    if "running" in states:
        data["state"] = "running"
    elif "failed" in states:
        data["state"] = "failed"
    elif all(s in ("completed", "skipped") for s in states):
        data["state"] = "completed"
        data["completed_at"] = _now_iso()
    else:
        data["state"] = "running"

    with atomic_output_file(target) as tmp_path:
        Path(tmp_path).write_text(to_yaml_string(data))
    return target


# ── Run config (identity + resume validation) ────────────────────


def _get_git_sha() -> str:
    """Return the short git commit SHA of HEAD, or empty string on failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return ""


def _write_run_config(  # noqa: PLR0913
    run_dir: Path,
    *,
    process_name: str,
    process_path: Path,
    run_id: str,
    variables: dict[str, str],
    backend: str,
    variant: str | None,
    execution_profile: str | None = None,
    artifact_namespace: str | None = None,
    resolved_profiles: list[dict[str, object]] | None = None,
    auth_flags: AuthPoolFlags | None = None,
    max_concurrency: int | None = None,
    resource_snapshot: ResourceRunSnapshot | None = None,
) -> Path:
    """Write run-config.yaml at run creation time; record changes on resume.

    Schema v2 adds an ``auth:`` block
    capturing the dispatch's auth-pool configuration and a
    ``concurrency:`` block capturing the operator's max-concurrency
    intent. On resume, compares the new flags against the persisted
    values and appends a ``dispatch_config_change`` event to
    ``.logs/dispatch-config-changes.jsonl`` rather than mutating the
    original config — preserves the audit trail per spec.

    Returns the path to the config file. Resume calls validate
    process / run_dir identity (raises CLIError on mismatch) but
    accept changes to auth / concurrency as recorded events.
    """
    config_path = paths_mod.run_config_file(run_dir)
    logs_dir = paths_mod.run_logs_dir(run_dir)
    if config_path.exists():
        _validate_run_config(config_path, process_name=process_name, run_dir=run_dir)
        # Compute and record any auth/concurrency diff so the
        # aggregator can render the resume timeline.
        _record_resume_config_change(
            config_path,
            logs_dir=logs_dir,
            new_auth_flags=auth_flags,
            new_max_concurrency=max_concurrency,
        )
        return config_path

    state_dir = paths_mod.run_state_dir(run_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    # Persist process_spec as an absolute path so consumers like
    # `metaproc status --steps` can rebuild the plan regardless of which
    # cwd they run from. A relative string would resolve against the
    # status command's cwd, not the launch cwd, and silently degrade to
    # "no plan available".
    data: dict[str, object] = {
        "metaproc_layout": RUN_LAYOUT_VERSION,
        "process": process_name,
        "process_spec": str(process_path.resolve()),
        "run_id": run_id,
        "run_dir": str(run_dir),
        "variables": variables,
        "backend": backend,
        "created_at": _now_iso(),
        "git_sha": _get_git_sha(),
    }
    if variant:
        data["variant"] = variant
    if execution_profile:
        data["execution_profile"] = execution_profile
    if artifact_namespace:
        data["artifact_namespace"] = artifact_namespace
    if resolved_profiles:
        data["resolved_profiles"] = resolved_profiles
    if auth_flags is not None and auth_flags.is_pool_dispatch_enabled():
        data["auth"] = _serialize_auth_flags(auth_flags)
    if max_concurrency is not None:
        # Both fields are the same on first write; concurrency_adjust
        # events in the runpool event stream track in-flight scaling.
        data["concurrency"] = {
            "initial": int(max_concurrency),
            "effective": int(max_concurrency),
        }
    if resource_snapshot is not None:
        data["resources"] = resource_snapshot.model_dump(mode="json", by_alias=True)

    with atomic_output_file(config_path) as tmp_path:
        Path(tmp_path).write_text(to_yaml_string(data))
    log.info("Wrote run-config.yaml: %s", config_path)
    return config_path


def _serialize_auth_flags(flags: AuthPoolFlags) -> dict[str, object]:
    """Render an :class:`AuthPoolFlags` as the on-disk ``auth:`` block.

    Empty-string scalar fields and empty-tuple labels are omitted so
    the YAML stays compact for the no-flag baseline.
    """
    out: dict[str, object] = {}
    if flags.auth_account:
        out["account"] = flags.auth_account
    if flags.auth_backend:
        out["backend"] = flags.auth_backend
    if flags.auth_fallback_policy:
        out["fallback_policy"] = flags.auth_fallback_policy
    if flags.auth_policy:
        out["selection_policy"] = flags.auth_policy
    if flags.auth_include_labels:
        out["include_labels"] = list(flags.auth_include_labels)
    if flags.auth_exclude_labels:
        out["exclude_labels"] = list(flags.auth_exclude_labels)
    if not flags.auth_cross_quota_group:
        out["cross_quota_group"] = False
    return out


def _preflight_plan_adapters(plan: Plan, *, active_step_ids: set[str]) -> list[str]:
    """Collect harness-CLI version-drift warnings for the adapters the active
    steps use, before any step runs.

    Adapters expose a ``preflight()`` that returns a drift message (or ``None``)
    for their pinned CLI. Surfacing this at launch turns a would-be mid-DAG
    surprise — discovered only when the first step using that adapter runs, after
    minutes of upstream work — into an immediate, prominent heads-up. It is
    deliberately **non-blocking**: a drifted CLI may behave differently from the
    pin, but stopping the run outright would get in the way of debugging, so we
    warn loudly and proceed. Returns the drift messages (empty if none).
    """
    messages: list[str] = []
    for adapter_type in dict.fromkeys(
        step.adapter.type for step in plan.steps if step.step_id in active_step_ids
    ):
        preflight = getattr(get_adapter(adapter_type), "preflight", None)
        if preflight is None:
            continue
        message = preflight()
        if message:
            messages.append(message)
    return messages


def _serialize_plan_profiles(plan: Plan) -> list[dict[str, object]]:
    """Render profile-related plan metadata for run-config.yaml."""
    profiles: dict[str, dict[str, object]] = {}
    for step in plan.steps:
        if not step.execution_profile:
            continue
        profiles.setdefault(
            step.execution_profile,
            {
                "name": step.execution_profile,
                "adapter": step.adapter.type,
                "config": step.adapter.config,
            },
        )
    return [profiles[name] for name in sorted(profiles)]


def _record_resume_config_change(
    config_path: Path,
    *,
    logs_dir: Path,
    new_auth_flags: AuthPoolFlags | None,
    new_max_concurrency: int | None,
) -> None:
    """Append a ``dispatch_config_change`` event when resume changes flags.

    Reads the persisted ``auth:`` + ``concurrency:`` blocks, computes
    the diff against the new args, and appends one event per change
    to ``.logs/dispatch-config-changes.jsonl``. No-op when nothing
    changed or when the new args carry no auth/concurrency context
    (legacy callers, tests).

    Per spec § Phase 3: emit events rather than mutating the original
    run-config so the aggregator sees the full timeline.
    """
    try:
        raw = read_yaml_file(config_path)
    except Exception:  # noqa: BLE001 — best-effort; never break resume
        return
    if not isinstance(raw, dict):
        return

    new_auth = (
        _serialize_auth_flags(new_auth_flags)
        if new_auth_flags is not None and new_auth_flags.is_pool_dispatch_enabled()
        else {}
    )
    saved_auth = raw.get("auth") or {}
    if not isinstance(saved_auth, dict):
        saved_auth = {}

    changes: list[dict[str, object]] = []
    auth_diff = _diff_dicts(saved_auth, new_auth)
    if auth_diff:
        changes.append({"field": "auth", "diff": auth_diff})

    if new_max_concurrency is not None:
        saved_conc = raw.get("concurrency") or {}
        saved_initial: int | None = None
        if isinstance(saved_conc, dict):
            saved_initial_raw = saved_conc.get("initial")
            if isinstance(saved_initial_raw, (int, str)) and saved_initial_raw not in ("", None):
                saved_initial = int(saved_initial_raw)
        if saved_initial != int(new_max_concurrency):
            changes.append(
                {
                    "field": "max_concurrency",
                    "diff": {"old": saved_initial, "new": int(new_max_concurrency)},
                }
            )

    if not changes:
        return

    logs_dir.mkdir(parents=True, exist_ok=True)
    event = {
        "event": "dispatch_config_change",
        "ts": _now_iso(),
        "changes": changes,
    }
    target = logs_dir / paths_mod.DISPATCH_CONFIG_CHANGES_FILE
    with target.open("a") as fh:
        fh.write(_json_dumps(event) + "\n")
    log.info("Recorded dispatch_config_change: %d field(s)", len(changes))


def _diff_dicts(old: dict[str, object], new: dict[str, object]) -> dict[str, object]:
    """Return a flat ``{key: {"old": ..., "new": ...}}`` for differing keys.

    Treats missing keys as ``None`` rather than dropping them — the
    aggregator needs to know "auth_policy was unset, now round-robin"
    just as much as "auth_policy was priority-order, now round-robin".
    """
    diff: dict[str, object] = {}
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key), new.get(key)
        if a != b:
            diff[key] = {"old": a, "new": b}
    return diff


def _json_dumps(value: object) -> str:
    """Single-line JSON for jsonl writers; lazy import keeps top-level lean."""

    return _json.dumps(value, default=str)


def _validate_run_config(
    config_path: Path,
    *,
    process_name: str,
    run_dir: Path,
) -> None:
    """Validate existing run-config.yaml matches current launch parameters.

    Raises CLIError on incompatible resume attempts.
    """
    raw = read_yaml_file(config_path)
    if not isinstance(raw, dict):
        raise CLIError(f"Corrupt run-config.yaml: {config_path}")

    saved_process = raw.get("process", "")
    if saved_process != process_name:
        raise CLIError(
            f"Resume mismatch: run-config.yaml has process={saved_process!r} "
            f"but you launched with process={process_name!r}. "
            f"Use a different RUN_ID for a new process."
        )

    saved_run_dir = raw.get("run_dir", "")
    if saved_run_dir and _resume_run_dir_identity(str(run_dir)) != _resume_run_dir_identity(
        saved_run_dir
    ):
        raise CLIError(
            f"Resume mismatch: run-config.yaml has run_dir={saved_run_dir!r} "
            f"but current run_dir={str(run_dir)!r}. "
            f"Use the same RUNS_DIR or RUN_ID that created this run."
        )

    log.info("Resume validated against run-config.yaml (%s)", config_path)


def _resume_run_dir_identity(run_dir: str) -> str:
    """Normalize run_dir for resume validation across Filestore mount aliases.

    Cloud and operator hosts can mount the same Filestore share at different
    prefixes (for example ``/mnt/disks/filestore`` vs ``/mnt/filestore`` vs a
    local workstation mount under ``.../mnt/filestore``). Normalize only those
    known Filestore aliases. All other paths keep their full normalized form so
    unrelated local ``runs/`` directories cannot collide.
    """
    normalized = os.path.normpath(run_dir)
    filestore_markers = (
        f"{os.sep}mnt{os.sep}disks{os.sep}filestore{os.sep}runs{os.sep}",
        f"{os.sep}mnt{os.sep}filestore{os.sep}runs{os.sep}",
    )
    for marker in filestore_markers:
        if marker in normalized:
            return (
                f"{os.sep}mnt{os.sep}filestore{os.sep}runs{os.sep}" + normalized.split(marker, 1)[1]
            )
    return normalized


# ── Completion detection ──────────────────────────────────────────


def _step_item_dir(run_dir: Path, step_id: str) -> Path:
    """Per-task state directory for a step.

    Under the new run-dir layout this is the directory that holds
    ``status.yaml`` / ``attempt.yaml`` / ``result.yaml`` for a non-fan-out
    step directly (no inner ``.state/`` subdir):
    ``<run>/.state/tasks/<step_id>/``.
    """

    return run_dir / _STATE_DIR / _TASKS_SUBDIR / step_id


def _resolve_step_item_dir(
    run_dir: Path,
    step_id: str,
    outputs: dict[str, IOSpec] | None,
    variables: dict[str, str],
    *,
    step_def: ProcessStep | None = None,
) -> Path:
    """Resolve the per-task state directory that owns a step's runtime state.

    For fan-out steps the caller must pass *step_def* so the per-task
    item-key can be resolved from ``for_each.key``. For non-fan-out
    steps the per-task dir is ``<run>/.state/tasks/<step_id>/``.
    """
    del outputs  # no longer used; state dir is derived from step + variables
    if step_def is not None:
        return compute_task_state_dir(run_dir, step_def, variables)
    return _step_item_dir(run_dir, step_id)


def _read_step_status(run_dir: Path, step_id: str) -> StatusRecord | None:
    """Read status.yaml for a step (non-fan-out)."""
    return read_status_at(_step_item_dir(run_dir, step_id))


def _read_process_status_yaml(run_dir: Path) -> dict[str, Any] | None:
    """Read ``.state/process-status.yaml`` as a raw dict, or ``None``.

    Authoritative step-level state lives here for every step — including
    fan-out steps, which do not write a canonical ``<step>/.state/status.yaml``
    of their own. Consumers that only consult the canonical path cannot
    detect a completed fan-out step. Use this as a fallback.
    """
    path = run_dir / STATE_DIR / "process-status.yaml"
    if not path.exists():
        return None
    try:
        data = read_yaml_file(path)
    except YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _read_recorded_step_hash(run_dir: Path, step_id: str) -> str | None:
    """Compat shim — defer to the engine-layer ``recorded_step_hash``."""
    return recorded_step_hash(run_dir, step_id)


def _declared_output_errors(
    outputs: dict[str, IOSpec] | None,
    variables: dict[str, str] | None,
    run_dir: Path,
) -> list[str]:
    """Validate completed-step outputs that can be resolved now."""
    if not outputs or variables is None:
        return []
    resolvable: dict[str, IOSpec] = {}
    for name, spec in outputs.items():
        path_template = spec.path
        if path_template is None:
            continue
        rendered = resolve_templates(path_template, variables)
        if "{{" in rendered or "}}" in rendered:
            continue
        resolvable[name] = spec
    if not resolvable:
        return []
    return validate_item_outputs(run_dir, resolvable, variables=variables)


def _is_step_completed(
    run_dir: Path,
    step_id: str,
    outputs: dict[str, IOSpec] | None = None,
    variables: dict[str, str] | None = None,
    *,
    is_fan_out: bool = False,
    step: ResolvedStep | None = None,
) -> bool:
    """Check if a step has completed.

    Checks, in order:

    1. The canonical step dir ``run_dir/<step_id>/.state/status.yaml``.
    2. ``run_dir/.state/process-status.yaml``, which is authoritative for
       step-level state and is written for every step transition —
       including fan-out steps that do not produce a step-level status
       file of their own.

    The fallback at (2) fixes ``--from`` / ``--only`` resumes after a
    completed fan-out ancestor; without it, `_verify_ancestors` reports
    "no completion record" even when the step succeeded.

    For non-fan-out steps, a completed verdict is also checked against
    declared outputs on disk. Fan-out item outputs require per-item context,
    so step-level fan-out completion is validated by process state.

    When *step* is provided (the resolved step from the current plan), the
    step's current fingerprint is compared to the persisted
    ``recorded_step_hash``. A mismatch demotes a completed verdict back to
    False so editing a runbook and rerunning against the same RUN_ID
    re-executes that step.
    A completion with no recorded fingerprint at all is treated as a
    legacy completion and kept as completed — old runs that pre-date the
    fingerprint mirror do NOT auto-rerun on upgrade.
    """
    decision: str | None = None

    # Check canonical per-task state dir at <run>/.state/tasks/<step_id>/.
    record = _read_step_status(run_dir, step_id)
    if record is not None and record.state == "completed":
        decision = "step status.yaml"

    # Fallback: process-status.yaml is authoritative for fan-out steps.
    if decision is None:
        ps = _read_process_status_yaml(run_dir)
        if ps is not None:
            steps = ps.get("steps") if isinstance(ps.get("steps"), dict) else None
            if steps is not None:
                step_entry = steps.get(step_id)
                if isinstance(step_entry, dict) and step_entry.get("state") == "completed":
                    decision = "process-status.yaml"

    if decision is None:
        return False

    output_errors = [] if is_fan_out else _declared_output_errors(outputs, variables, run_dir)
    if output_errors:
        log.warning(
            "step '%s' marked completed via %s but declared outputs do not validate; "
            "treating as not completed and re-running. Errors: %s",
            step_id,
            decision,
            "; ".join(output_errors),
        )
        return False

    if step is not None:
        recorded = _read_recorded_step_hash(run_dir, step_id)
        if recorded is None:
            log.info(
                "step '%s' marked completed via %s but has no recorded fingerprint; "
                "treating as legacy completion (will not auto-rerun on upgrade)",
                step_id,
                decision,
            )
        else:
            current = fingerprint_step(step)
            if recorded != current:
                log.info(
                    "step '%s' marked completed via %s but fingerprint changed "
                    "(was %s, now %s); treating as not completed and re-running",
                    step_id,
                    decision,
                    recorded,
                    current,
                )
                return False
    return True


def _maybe_cascade_for_fingerprint(
    run_dir: Path,
    plan: Plan,
    step: ResolvedStep,
    *,
    variables: dict[str, str] | None,
    out: Any,
) -> list[str]:
    """Invalidate *step* and everything downstream when its fingerprint has
    changed since the prior completion.

    No-op when no fingerprint was ever recorded (legacy completion) or when
    the recorded fingerprint still matches. Returns the list of step IDs
    whose per-task ``status.yaml`` was renamed to ``.stale`` (empty when
    nothing was invalidated).

    Companion to the fingerprint check inside ``_is_step_completed``: the
    in-process check returns False so the changed step itself re-runs;
    this helper renames the downstream per-task status files so descendants
    also re-execute.
    """
    recorded = _read_recorded_step_hash(run_dir, step.step_id)
    if recorded is None:
        return []
    current = fingerprint_step(step)
    if recorded == current:
        return []
    invalidated = _invalidate_downstream(run_dir, step.step_id, plan, variables=variables)
    if invalidated:
        out.progress(
            f"  Step '{step.step_id}': fingerprint changed (was {recorded}, now {current})"
            f" — invalidated: {', '.join(invalidated)}"
        )
    return invalidated


def _invalidate_downstream(
    run_dir: Path,
    root_step_id: str,
    plan: Plan,
    *,
    variables: dict[str, str] | None = None,
) -> list[str]:
    """Rename status.yaml to .stale for root and all downstream steps.

    Walks all per-task state dirs under ``<run>/.state/tasks/<step_id>/``
    (covers both the non-fan-out ``status.yaml`` and the per-item
    fan-out ``<key>/status.yaml`` files).
    """
    del variables  # state dirs are keyed by step_id, not by output template
    dep_ids = downstream(plan.steps, root_step_id)
    all_ids = [root_step_id, *dep_ids]
    invalidated: list[str] = []

    for step_id in all_ids:
        step_state = _step_item_dir(run_dir, step_id)
        status_paths: list[Path] = []
        non_fan_out_status = step_state / STATUS_FILE
        if non_fan_out_status.exists():
            status_paths.append(non_fan_out_status)
        if step_state.exists():
            for sub in step_state.iterdir():
                if sub.is_dir():
                    fan_out_status = sub / STATUS_FILE
                    if fan_out_status.exists():
                        status_paths.append(fan_out_status)

        step_invalidated = False
        for status_path in status_paths:
            stale_path = status_path.with_suffix(".yaml.stale")
            status_path.rename(stale_path)
            step_invalidated = True
        if step_invalidated:
            invalidated.append(step_id)

    return invalidated


# ── Ancestor verification ─────────────────────────────────────────


def _read_overrides_for_verify(run_dir: Path) -> OverridesDocument | None:
    """Read overrides once for ancestor verification. Swallowed on error."""
    try:
        return read_overrides(run_dir)
    except Exception:
        log.debug("Could not read overrides for %s", run_dir, exc_info=True)
        return None


def _verify_ancestors(
    run_dir: Path,
    plan: Plan,
    active_step_ids: set[str],
    spec: ProcessSpec,
    variables: dict[str, str],
) -> list[str]:
    """Verify omitted ancestors of active steps have completed.

    Returns list of error messages for unsatisfied ancestors.
    Checks the overrides document once: if an ancestor is overridden
    for the requesting downstream, the error is suppressed.
    """
    all_step_ids = {s.step_id for s in plan.steps}
    omitted = all_step_ids - active_step_ids
    step_map = {s.step_id: s for s in plan.steps}
    errors: list[str] = []

    # Read overrides once for the whole verification pass
    overrides_doc = _read_overrides_for_verify(run_dir)

    # Build needs for active steps that reference omitted steps
    for step in plan.steps:
        if step.step_id not in active_step_ids:
            continue
        for dep in step.needs:
            if dep in omitted:
                dep_target = step_map.get(dep)
                dep_outputs = dep_target.outputs if dep_target else None
                dep_is_fan_out = dep_target.fan_out is not None if dep_target else False
                if not _is_step_completed(
                    run_dir,
                    dep,
                    outputs=dep_outputs,
                    variables=variables,
                    is_fan_out=dep_is_fan_out,
                    step=dep_target,
                ):
                    # Check override before recording the error
                    if overrides_doc is not None and is_step_overridden(
                        overrides_doc, dep, step.step_id
                    ):
                        _ov_entries = overrides_for_step(overrides_doc, dep)
                        _ov_note = _ov_entries[0].note if _ov_entries else ""
                        log.info(
                            "Ancestor %s satisfied via override: %r",
                            dep,
                            _ov_note,
                        )
                        continue
                    record = _read_step_status(run_dir, dep)
                    state = record.state if record else "no completion record"
                    errors.append(f"  {dep}: {state}")

    return errors


# ── Step execution dispatch ───────────────────────────────────────


async def _execute_code_step(
    *,
    spec: ProcessSpec,
    step_def: ProcessStep,
    target: ResolvedStep,
    variables: dict[str, str],
    process_dir: Path,
    run_dir: Path,
    run_id: str,
    out: Any,
) -> bool:
    """Execute a mode:code step. Returns True on success."""
    step_id = target.step_id
    step_hash = fingerprint_step(target)
    try:
        validate_step_inputs_exist(target.inputs, variables, context=f"step '{step_id}'")
    except ValueError as exc:
        raise CLIError(str(exc)) from exc
    handler_ref = target.handler
    command_ref = target.command

    if not handler_ref and not command_ref:
        raise CLIError(f"step '{step_id}' is mode:code but has no handler or command")

    effective_outputs = target.outputs
    state_dir = compute_task_state_dir(run_dir, step_def, variables)
    artifact_dir = compute_item_dir(effective_outputs, variables)

    state_dir.mkdir(parents=True, exist_ok=True)
    item_record = {"step": step_id}
    running_record = mark_running_at(state_dir, run_id=run_id, step_id=step_id, item=item_record)

    handler_fn = None
    if handler_ref:
        handler_fn = resolve_code_handler(handler_ref, process_dir)

    # Captured code stdout/stderr is a task log, not a run-level stream.
    logs_dir = compute_task_logs_dir(run_dir, step_def, variables)
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    log_file = logs_dir / f"process_{ts}.log"

    try:
        if handler_fn is not None:
            process_step = step_def.model_copy(
                deep=True,
                update={"inputs": target.inputs, "outputs": target.outputs},
            )
            with sample_step_resources(
                run_dir=run_dir,
                run_id=run_id,
                step_node_id=step_id,
            ):
                # Off the event loop: a code handler is synchronous, and calling it
                # inline pins the loop for its whole duration, which serializes every
                # sibling item of a fan-out no matter how the dispatcher gathers them.
                await asyncio.to_thread(handler_fn, dict(variables), process_step)
        elif command_ref is not None:
            resolved_cmd = resolve_templates(command_ref, variables)
            env = dict(os.environ)
            if target.env:
                env.update({k: resolve_templates(v, variables) for k, v in target.env.items()})
            result = run_sampled_step_command(
                shlex.split(resolved_cmd),
                env=env,
                cwd=process_dir,
                run_dir=run_dir,
                run_id=run_id,
                step_node_id=step_id,
            )
            # Capture stdout/stderr to log file
            output = ""
            if result.stdout:
                output += result.stdout
            if result.stderr:
                output += result.stderr
            if output:
                with atomic_output_file(log_file) as tmp_path:
                    tmp_path.write_text(output)
    except subprocess.CalledProcessError as exc:
        # Capture output even on failure
        output = ""
        if exc.stdout:
            output += exc.stdout
        if exc.stderr:
            output += exc.stderr
        if output:
            with atomic_output_file(log_file) as tmp_path:
                tmp_path.write_text(output)
        command_error = f"command exit code {exc.returncode}"
        mark_failed_at(
            state_dir,
            error=command_error,
            running_record=running_record,
            failure_class=str(classify_failure(command_error)),
        )
        return False
    except Exception as exc:
        tb = traceback.format_exc()
        with atomic_output_file(log_file) as tmp_path:
            tmp_path.write_text(tb)
        handler_error = f"{type(exc).__name__}: {exc} (traceback in {log_file.name})"
        mark_failed_at(
            state_dir,
            error=handler_error,
            running_record=running_record,
            failure_class=str(classify_failure(handler_error)),
        )
        return False

    if effective_outputs and artifact_dir is not None:
        output_failures = validate_item_outputs_detailed(
            artifact_dir, effective_outputs, variables=variables
        )
        if output_failures:
            output_errors = [f.summary() for f in output_failures]
            mark_failed_at(
                state_dir,
                error=f"output validation failed: {'; '.join(output_errors)}",
                running_record=running_record,
                output_failures=output_failures,
                failure_class=str(FailureClass.INVALID_OUTPUT),
            )
            return False

    mark_completed_at(state_dir, running_record=running_record)
    write_result_at(
        state_dir,
        ResultRecord(
            run_id=run_id,
            step_id=step_id,
            state="completed",
            validated=True,
            outputs=resolve_record_output_paths(effective_outputs, variables),
            published_at=_now_iso(),
            step_hash=step_hash,
        ),
    )
    return True


def _contract_retry_decision(
    *,
    step_map: dict[str, ResolvedStep],
    step_def_map: dict[str, ProcessStep],
    run_dir: Path,
) -> Callable[[str, dict[str, str]], bool]:
    """Decide retryability from what refused the output, per the output's declaration.

    Nothing here interprets a failure. The producing output declares what its contract
    failure costs through ``on_invalid``, and ``classify_output_failures`` resolves that
    by reading the refusing invariant rather than the sentence describing it. Keeping the
    decision on the contract is what stops every consumer from re-deciding it.

    A failure with no structured record is not a contract failure, so it is left alone:
    the operational path owns those.
    """

    def _should_retry(step_id: str, item_vars: dict[str, str]) -> bool:
        state_dir = compute_task_state_dir(run_dir, step_def_map[step_id], item_vars)
        try:
            status = read_status_at(state_dir)
        except (ValueError, TypeError, ValidationError):
            return False
        failures = getattr(status, "output_failures", None) or []
        if not failures:
            return False
        return classify_output_failures(failures, step_map[step_id].outputs) == RetryVerdict.RETRY

    return _should_retry


def _with_declared_retry(
    invoke: StepInvoker,
    *,
    step_map: dict[str, ResolvedStep],
    step_def_map: dict[str, ProcessStep],
    run_dir: Path,
) -> StepInvoker:
    """Apply each step's own declared retry budget.

    Resolved per step rather than once for the caller, because a chain runs several
    steps and the budget is declared on the step that has a reason to retry. Applying
    the first step's policy to the whole walk would give a stage a budget nobody
    declared for it, and silently withhold one that was.
    """

    def _budget(step_id: str) -> tuple[int, Callable[[int], float]] | None:
        fan_out = step_map[step_id].fan_out
        policy = fan_out.retry if fan_out else None
        if policy is None:
            return None
        return (policy.max_retries + 1, lambda attempt: compute_backoff(attempt, policy))

    return with_retry(
        invoke,
        should_retry=_contract_retry_decision(
            step_map=step_map, step_def_map=step_def_map, run_dir=run_dir
        ),
        budget_for=_budget,
    )


def _discover_chain_items(
    *,
    target: ResolvedStep,
    step_def: ProcessStep,
    variables: dict[str, str],
    run_dir: Path,
) -> list[dict[str, str]]:
    """Every item of a fan-out source, whether or not it has already run.

    Reuse is decided per item per step by the caller rather than here, because an item
    that finished one step of a chain but not a later one is still actionable and
    filtering it out at discovery would drop it from the chain entirely.
    """
    assert target.fan_out is not None
    source_path = Path(target.fan_out.source)
    if not source_path.exists():
        raise CLIError(f"source file not found: {source_path}")
    try:
        discovery = discover_items_from_source(
            source_path,
            step_def,
            output_paths=None,
            params=variables,
            reuse_policy="trust_state",
            run_dir=run_dir,
        )
    except ValueError as exc:
        raise CLIError(str(exc)) from exc
    return [*discovery.actionable_contexts, *discovery.filtered_items]


async def _execute_code_fan_out_step(
    *,
    spec: ProcessSpec,
    step_def: ProcessStep,
    target: ResolvedStep,
    variables: dict[str, str],
    process_dir: Path,
    run_dir: Path,
    run_id: str,
    max_concurrency: int | None,
    external_semaphore: asyncio.Semaphore | None,
    out: Any,
) -> bool:
    """Execute a mode:code step with for_each, one handler invocation per item."""
    step_id = target.step_id
    assert target.fan_out is not None

    item_contexts = _discover_chain_items(
        target=target, step_def=step_def, variables=variables, run_dir=run_dir
    )
    if not item_contexts:
        out.progress(f"  Step '{step_id}': no actionable items (all completed)")
        return True

    out.progress(f"  Step '{step_id}': {len(item_contexts)} actionable items")

    async def _invoke(_step_id: str, item_vars: dict[str, str]) -> bool:
        return await _execute_code_step(
            spec=spec,
            step_def=step_def,
            target=target,
            variables=item_vars,
            process_dir=process_dir,
            run_dir=run_dir,
            run_id=run_id,
            out=out,
        )

    succeeded, total = await run_fan_out(
        item_contexts=item_contexts,
        variables=variables,
        invoke=_with_declared_retry(
            _invoke,
            step_map={step_id: target},
            step_def_map={step_id: step_def},
            run_dir=run_dir,
        ),
        step_id=step_id,
        max_concurrency=max_concurrency,
        step_concurrency=target.fan_out.max_concurrency,
        external_semaphore=external_semaphore,
    )
    if succeeded != total:
        out.progress(f"  Step '{step_id}': {total - succeeded} of {total} items failed")
    return succeeded == total


async def _execute_item_aligned_chain(
    *,
    spec: ProcessSpec,
    chain: list[str],
    step_map: dict[str, ResolvedStep],
    step_def_map: dict[str, ProcessStep],
    variables: dict[str, str],
    process_dir: Path,
    run_dir: Path,
    run_id: str,
    max_concurrency: int | None,
    external_semaphore: asyncio.Semaphore | None,
    out: Any,
) -> dict[str, bool]:
    """Run an item-aligned chain once per item, and report success per step."""
    head = step_map[chain[0]]
    assert head.fan_out is not None

    item_contexts = _discover_chain_items(
        target=head, step_def=step_def_map[chain[0]], variables=variables, run_dir=run_dir
    )
    if not item_contexts:
        out.progress(f"  Chain '{' -> '.join(chain)}': no items")
        return dict.fromkeys(chain, True)

    out.progress(f"  Chain '{' -> '.join(chain)}': {len(item_contexts)} items, item-aligned")

    async def _invoke(step_id: str, item_vars: dict[str, str]) -> bool:
        return await _execute_code_step(
            spec=spec,
            step_def=step_def_map[step_id],
            target=step_map[step_id],
            variables=item_vars,
            process_dir=process_dir,
            run_dir=run_dir,
            run_id=run_id,
            out=out,
        )

    def _is_done(step_id: str, item_vars: dict[str, str]) -> bool:
        prior = read_status_at(compute_task_state_dir(run_dir, step_def_map[step_id], item_vars))
        return prior is not None and prior.state in ("completed", "cached")

    def _step_concurrency(step_id: str) -> int | None:
        fan_out = step_map[step_id].fan_out
        return fan_out.max_concurrency if fan_out else None

    tallies = await run_aligned_chain(
        chain=chain,
        item_contexts=item_contexts,
        variables=variables,
        invoke=_with_declared_retry(
            _invoke,
            step_map=step_map,
            step_def_map=step_def_map,
            run_dir=run_dir,
        ),
        is_done=_is_done,
        max_concurrency=max_concurrency,
        step_concurrency=_step_concurrency,
        external_semaphore=external_semaphore,
    )

    results: dict[str, bool] = {}
    for step_id, (succeeded, reached) in tallies.items():
        results[step_id] = reached > 0 and succeeded == reached
        if not results[step_id]:
            out.progress(f"  Step '{step_id}': {reached - succeeded} of {reached} items failed")
    return results


async def _run_agent_subprocess(
    cmd: list[str],
    *,
    env: dict[str, str],
    cwd: Path | None,
    log_path: Path,
    timeout_s: int | None,
    use_filter: bool,
) -> int:
    """Run one blocking adapter subprocess without blocking the DAG event loop."""

    def _run() -> int:
        if use_filter:
            from metaproc.engine.runtime import (  # noqa: PLC0415 -- optional filter path
                start_log_filter_thread,
            )

            log_fh = log_path.open("w")
            try:
                fg_proc = subprocess.Popen(
                    cmd,
                    env=env,
                    cwd=cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                stdout_pipe = fg_proc.stdout
                if stdout_pipe is None:
                    msg = "adapter subprocess did not expose the requested stdout pipe"
                    raise RuntimeError(msg)
                ft = start_log_filter_thread(stdout_pipe, log_fh)
                fg_proc.wait(timeout=timeout_s)
                ft.join(timeout=5.0)
                return fg_proc.returncode
            finally:
                log_fh.close()

        with log_path.open("w") as log_fh:
            completed = subprocess.run(
                cmd,
                env=env,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=timeout_s,
            )
        return completed.returncode

    return await asyncio.to_thread(_run)


async def _execute_agent_step(
    *,
    spec: ProcessSpec,
    step_def: ProcessStep,
    target: ResolvedStep,
    variables: dict[str, str],
    process_dir: Path,
    run_dir: Path,
    run_id: str,
    variant_override: str | None = None,
    peer_allowed_targets: list[WriteTarget] | None = None,
    backend_name: str = "local",
    out: Any,
) -> bool:
    """Execute a mode:agent step (no for_each). Returns True on success."""

    step_id = target.step_id
    step_hash = fingerprint_step(target)
    merged = merge_defaults(step_def, spec.defaults)

    adapter_type = target.adapter.type
    merged_config = dict(target.adapter.config)
    if merged.get("max_budget_usd") is not None:
        merged_config.setdefault("max_budget_usd", merged["max_budget_usd"])
    runtime_config = cast("dict[str, object]", resolve_runtime_config(merged_config, variables))

    derived_variant = derive_variant(adapter_type, runtime_config)
    effective_execution_profile = (
        target.execution_profile
        or step_def.execution_profile
        or variant_override
        or derived_variant
    )
    effective_variant = (
        target.artifact_namespace
        or step_def.artifact_namespace
        or step_def.variant
        or variant_override
        or derived_variant
    )
    step_vars = dict(variables)
    step_vars["EXECUTION_PROFILE"] = effective_execution_profile
    step_vars["ARTIFACT_NAMESPACE"] = effective_variant
    step_vars["VARIANT"] = effective_variant

    effective_outputs = target.outputs
    adapter_obj = get_adapter(adapter_type)

    try:
        validate_step_inputs_exist(target.inputs, step_vars, context=f"step '{step_id}'")
    except ValueError as exc:
        raise CLIError(str(exc)) from exc

    resolved_prompt, logs_dir, log_path = prepare_step(
        spec,
        step_def,
        step_vars,
        process_dir,
        effective_outputs or None,
        prompt_paths=target.prompt_paths,
    )

    allowed_runtime = collect_step_runtime_placeholders(step_def)
    optional_unset = {name for name in spec.optional_input_names if name not in step_vars}
    enforce_no_unresolved_placeholders(
        resolved_prompt,
        context=f"step '{step_id}' prompt",
        allowed=allowed_runtime | optional_unset,
    )

    allowed_targets = _dedupe_write_targets(
        collect_write_targets(
            step_def,
            effective_outputs,
            step_vars,
            step_scope=False,
        )
        + list(peer_allowed_targets or [])
    )
    ignored_targets = _dedupe_write_targets(
        [
            WriteTarget(logs_dir, "tree"),
            WriteTarget(run_dir / LOGS_DIR, "tree"),
        ]
    )

    # State for a non-for_each agent step lives at <run>/.state/tasks/<step_id>/,
    # keyed by step_id. Two non-for_each steps that share an output parent
    # directory keep their state separate because the state dir does not
    # depend on the output path.
    state_dir = compute_task_state_dir(run_dir, step_def, step_vars)
    artifact_dir = compute_item_dir(effective_outputs, step_vars)
    state_dir.mkdir(parents=True, exist_ok=True)

    item_record = {"step": step_id}
    from metaproc.commands.run_parallel import (  # noqa: PLC0415 -- circular-import guard
        _resolve_retry_policy,
    )

    # An output that fails its contract is not always terminal: a stochastic producer
    # may get it right on a second pass. The ``on_invalid`` clause on the output says
    # which failures are worth another call, and the content-failure cap bounds how
    # many. Transient exit-code failures are classified below and draw the full
    # retry budget; step timeouts and boundary violations stay terminal here.
    retry_policy = _resolve_retry_policy(step_def, spec.defaults)
    content_retry_cap = max_retries_for(FailureClass.INVALID_OUTPUT, retry_policy.max_retries)
    attempt = 1
    output_failure_feedback: Sequence[OutputFailure] = ()

    while True:
        running_record = mark_running_at(
            state_dir, run_id=run_id, step_id=step_id, item=item_record, attempt=attempt
        )
        # Each retry keeps its own log, so the attempt that failed its contract stays
        # readable beside the one that replaced it.
        attempt_log_path = (
            log_path
            if attempt == 1
            else log_path.with_name(f"{log_path.stem}-attempt{attempt}{log_path.suffix}")
        )

        # Write the prompt before asking the adapter to build its command. Adapters that
        # inline prompt contents (notably codex-cli) must receive a real, readable path;
        # a synthetic placeholder makes the run fail before dispatch.
        ts = datetime.now(tz=UTC).strftime("%H%M%S")
        prompt_file = logs_dir / f"prompt-{step_id}-{ts}.txt"
        attempt_prompt = append_output_failure_feedback(resolved_prompt, output_failure_feedback)
        with atomic_output_file(prompt_file) as tmp_path:
            Path(tmp_path).write_text(attempt_prompt)

        cmd = adapter_obj.build_command(prompt_file, runtime_config, step_vars)
        write_attempt_at(
            state_dir,
            AttemptRecord(
                run_id=run_id,
                step_id=step_id,
                item=item_record,
                params=dict(step_vars),
                outputs=resolve_record_output_paths(effective_outputs, step_vars),
                runtime={
                    "adapter_type": adapter_type,
                    "execution_profile": effective_execution_profile,
                    "artifact_namespace": effective_variant,
                    "variant": effective_variant,
                    "command": cmd,
                },
                step_hash=step_hash,
            ),
        )

        boundary_before = capture_repo_snapshot(process_dir, observation_zone=[run_dir])

        env = adapter_obj.prepare_env(dict(os.environ), runtime_config)
        if target.env:
            env.update({k: resolve_templates(v, step_vars) for k, v in target.env.items()})
        cwd = adapter_obj.working_directory(runtime_config)
        timeout_s = runtime_config.get("timeout_s")
        timeout_val = int(str(timeout_s)) if timeout_s is not None else None

        use_filter = adapter_type == "pi-cli"
        # A step without for_each still launches a real agent process, so it takes a host
        # slot like any pool-launched attempt. Without this, N orchestrators on one machine
        # cannot see each other's scalar launches at all.
        scalar_resource_config = target.resources or runtime_config
        try:
            async with admitted_launch(
                enabled=backend_name == "local",
                limit=resolve_host_max_concurrency(
                    scalar_resource_config, default=SCALAR_DEFAULT_HOST_LIMIT
                ),
                label=f"{run_id}/{step_id}",
                pool_id=f"run-process:{run_id}",
                metadata={"backend": backend_name, "step_id": step_id, "mode": "agent"},
            ):
                exit_code = await _run_agent_subprocess(
                    cmd,
                    env=env,
                    cwd=cwd,
                    log_path=attempt_log_path,
                    timeout_s=timeout_val,
                    use_filter=use_filter,
                )
        except subprocess.TimeoutExpired:
            mark_failed_at(
                state_dir,
                error=f"timeout after {timeout_s}s",
                running_record=running_record,
                failure_class=str(FailureClass.TIMEOUT),
            )
            return False

        # Read the log before compacting it: the exit code alone says nothing about
        # why the agent died, and the answer is in the last few lines. A bare
        # "exit code 1" also classifies as a crash, so without this the transient
        # failures below are indistinguishable from a prompt that always fails.
        exit_error: str | None = None
        if exit_code != 0:
            exit_error = f"exit code {exit_code}"
            log_error = extract_log_error(attempt_log_path)
            if log_error:
                exit_error = f"{exit_error} (log: {log_error})"

        try_compact_log(attempt_log_path)

        if exit_error is not None:
            # A response-body timeout returns no tokens at all and is the most
            # retryable failure an agent produces. classify_error checks the
            # permanent patterns first, so quota, OOM, and permission failures
            # still fail on the first attempt.
            verdict = classify_error(exit_error)
            cap = max_retries_for(classify_failure(exit_error), retry_policy.max_retries)
            if verdict == RetryVerdict.RETRY and attempt <= cap:
                backoff = compute_backoff(attempt, retry_policy)
                out.progress(
                    f"  Step '{step_id}': retryable ({exit_error}), "
                    f"retry in {backoff:.0f}s (attempt {attempt}/{cap + 1})"
                )
                await asyncio.sleep(backoff)
                attempt += 1
                continue
            mark_failed_at(
                state_dir,
                error=exit_error,
                running_record=running_record,
                failure_class=str(classify_failure(exit_error)),
            )
            return False

        if boundary_before is not None and allowed_targets:
            boundary_after = capture_repo_snapshot(process_dir, observation_zone=[run_dir])
            violations = filter_boundary_violations(
                repo_changes_since(boundary_before, boundary_after),
                allowed=allowed_targets,
                ignored=ignored_targets,
                base_dir=boundary_after.repo_root if boundary_after else None,
            )
            if violations:
                mark_failed_at(
                    state_dir,
                    error=(
                        "write boundary violated: "
                        f"{_format_boundary_paths(violations, base_dir=boundary_after.repo_root if boundary_after else None)}"
                    ),
                    running_record=running_record,
                )
                return False

        if effective_outputs and artifact_dir is not None:
            # A freshly-emitted LLM artifact is the one input yaml_repair is scoped to:
            # a single unquoted colon in a note should not cost the whole step.
            for repaired in repair_declared_outputs(
                artifact_dir, effective_outputs, variables=step_vars
            ):
                out.progress(f"  Step '{step_id}': repaired YAML in {repaired.name}")
            # Then the question repair cannot ask, because it has no schema: does
            # each scalar say the type its contract asks for? An agent writing YAML
            # by hand has no serializer in the path, so a name like `1850` arrives
            # as an integer.
            for conformed in conform_declared_outputs(
                artifact_dir, effective_outputs, variables=step_vars
            ):
                out.progress(f"  Step '{step_id}': conformed scalars in {conformed.name}")
            # step_vars (not variables): only step_vars has VARIANT bound to
            # effective_variant. Without it, output paths containing {{run.variant}}
            # render with the literal placeholder and fpath.exists() reports false
            # even when the agent wrote the artifact at the correct path. A
            # production smoke exposed this: artifacts existed on disk, but the
            # unresolved placeholder made the step transition to FAILED.
            output_failures = validate_item_outputs_detailed(
                artifact_dir, effective_outputs, variables=step_vars
            )
            if output_failures:
                output_errors = [f.summary() for f in output_failures]
                error_str = f"output validation failed: {'; '.join(output_errors)}"
                verdict = classify_output_failures(output_failures, effective_outputs)
                if verdict == RetryVerdict.RETRY and attempt <= content_retry_cap:
                    output_failure_feedback = output_failures
                    backoff = compute_backoff(attempt, retry_policy)
                    out.progress(
                        f"  Step '{step_id}': retryable ({error_str}), "
                        f"retry in {backoff:.0f}s (attempt {attempt}/{content_retry_cap + 1})"
                    )
                    await asyncio.sleep(backoff)
                    attempt += 1
                    continue
                mark_failed_at(
                    state_dir,
                    error=error_str,
                    running_record=running_record,
                    output_failures=output_failures,
                    failure_class=str(FailureClass.INVALID_OUTPUT),
                )
                return False

        break

    mark_completed_at(state_dir, running_record=running_record)
    write_result_at(
        state_dir,
        ResultRecord(
            run_id=run_id,
            step_id=step_id,
            state="completed",
            validated=True,
            outputs=resolve_record_output_paths(effective_outputs, step_vars),
            published_at=_now_iso(),
            step_hash=step_hash,
        ),
    )
    return True


async def _execute_composite_step(
    *,
    step_def: ProcessStep,
    target: ResolvedStep,
    variables: dict[str, str],
    process_dir: Path,
    run_dir: Path,
    run_id: str,
    backend_name: str,
    max_concurrency: int | None,
    initial_concurrency: int | None = None,
    num_workers: int,
    machine_type: str,
    spot: bool,
    variant_override: str | None,
    profile_files: Sequence[Path] = (),
    external_semaphore: asyncio.Semaphore | None = None,
    out: Any,
    pool_dispatch_template: PoolDispatchConfig | None = None,
    auth_flags: AuthPoolFlags = AuthPoolFlags(),
    preflight_quota_guard: str = "warn",
    continue_on_step_failure: bool = False,
) -> bool:
    """Execute a mode:composite step by loading and running a child process spec.

    Auth-pool config (`pool_dispatch_template`, `auth_flags`,
    `preflight_quota_guard`) propagates from parent to child. Without this,
    the child's `run_parallel` invocations get `pool_dispatch=None` and bypass
    the pool entirely — Claude calls fall back to ambient `~/.claude` creds
    (the operator's interactive-login session) instead of the pool labels.
    Surfaced 2026-05-21: large workflow tier1 ran 3 hours of Claude calls but
    `metaproc auth usage <tier1>` reported 0 invocations on alt1/alt2.
    """
    step_id = step_def.id

    if not target.uses_path:
        raise CLIError(f"step '{step_id}': composite mode requires a resolved child process")

    child_spec_path = Path(target.uses_path)
    if not child_spec_path.exists():
        raise CLIError(f"step '{step_id}': child spec not found: {child_spec_path}")

    out.progress(f"  Step '{step_id}': loading child spec from {child_spec_path}")

    child_spec = load_process_spec(child_spec_path)
    child_process_dir = child_spec_path.parent

    # Build child variables: start with parent variables, overlay with_ mappings
    child_vars = dict(variables)
    if step_def.with_:
        for key, template in step_def.with_.items():
            child_vars[key] = resolve_templates(template, variables)
    child_vars = expand_process_vars(child_spec, child_vars, process_dir=child_process_dir)

    child_placeholder_errors = validate_spec_placeholders(child_spec, child_vars)
    if child_placeholder_errors:
        raise CLIError(
            "child process placeholder validation failed:\n  "
            + "\n  ".join(child_placeholder_errors)
        )

    child_input_errors = validate_process_inputs(child_spec, child_vars, child_process_dir)
    if child_input_errors:
        raise CLIError(
            "child process input validation failed:\n  " + "\n  ".join(child_input_errors)
        )

    child_plan = build_plan(
        child_spec,
        child_vars,
        process_path=child_spec_path,
        adapter_override=variant_override,
        artifact_namespace=target.artifact_namespace,
        profile_files=profile_files,
    )

    # Scope child run dir under parent: {parent_run_dir}/{step_id}/
    child_run_dir = run_dir / step_id
    child_run_dir.mkdir(parents=True, exist_ok=True)

    child_run_id = f"{run_id}/{step_id}"

    try:
        with ProcessEventLogger(paths_mod.process_events_log(child_run_dir)) as events:
            await _orchestrate(
                spec=child_spec,
                plan=child_plan,
                variables=child_vars,
                process_path=child_spec_path,
                process_dir=child_process_dir,
                run_dir=child_run_dir,
                run_id=child_run_id,
                backend_name=backend_name,
                max_concurrency=max_concurrency,
                initial_concurrency=initial_concurrency,
                num_workers=num_workers,
                machine_type=machine_type,
                spot=spot,
                variant_override=variant_override,
                profile_files=profile_files,
                skip_steps=set(),
                force=False,
                continue_on_error=continue_on_step_failure,
                out=out,
                events=events,
                pool_dispatch_template=pool_dispatch_template,
                auth_flags=auth_flags,
                preflight_quota_guard=preflight_quota_guard,
                continue_on_step_failure=continue_on_step_failure,
            )
    except CLIError as exc:
        # Surface the inner failure message — without this the operator
        # sees `Step '<composite>': FAILED` with no reason. Caught during
        # 2026-05-13 production rerun where two dispatches died at the
        # bundle→agent boundary with no visible error.
        out.progress(f"  Step '{step_id}': inner CLIError — {exc}")
        return False

    return True


def _current_worker_id() -> str | None:
    """Return the current worker id from env, if any."""
    worker_id = MetaprocEnv.METAPROC_WORKER_ID.read_str(default="")
    return worker_id or None


def _emit_fan_out_item_starts(
    events: ProcessEventLogger,
    *,
    step_id: str,
    each: str,
    item_contexts: list[dict[str, str]],
) -> None:
    """Emit one item_start event per item dispatched to a fan-out step."""
    worker_id = _current_worker_id()
    for item_context in item_contexts:
        events.item_start(step_id, item_context[each], worker_id=worker_id)


def _emit_fan_out_item_results(
    events: ProcessEventLogger,
    *,
    step_id: str,
    results: list[tuple[str, int]],
) -> None:
    """Emit one item_complete or item_fail event per finished fan-out item."""
    worker_id = _current_worker_id()
    for item, exit_code in results:
        if exit_code == 0:
            events.item_complete(step_id, item, worker_id=worker_id)
        else:
            events.item_fail(
                step_id,
                item,
                error=f"exit code {exit_code}",
                worker_id=worker_id,
            )


async def _execute_fan_out_step(
    *,
    spec: ProcessSpec,
    step_def: ProcessStep,
    target: ResolvedStep,
    variables: dict[str, str],
    process_path: Path,
    process_dir: Path,
    run_dir: Path,
    run_id: str,
    backend_name: str,
    max_concurrency: int | None,
    initial_concurrency: int | None = None,
    num_workers: int,
    machine_type: str,
    spot: bool,
    variant_override: str | None,
    profile_files: Sequence[Path] = (),
    external_semaphore: asyncio.Semaphore | None = None,
    peer_allowed_targets: list[WriteTarget] | None = None,
    events: ProcessEventLogger | None = None,
    out: Any,
    pool_dispatch_template: PoolDispatchConfig | None = None,
    auth_flags: AuthPoolFlags = AuthPoolFlags(),
    preflight_quota_guard: str = "warn",
) -> bool:
    """Execute a mode:agent step with for_each (fan-out). Returns True on success."""

    step_id = target.step_id
    merged = merge_defaults(step_def, spec.defaults)
    assert target.fan_out is not None
    each = target.fan_out.bind

    adapter_type = target.adapter.type
    merged_config = dict(target.adapter.config)
    if merged.get("max_budget_usd") is not None:
        merged_config.setdefault("max_budget_usd", merged["max_budget_usd"])
    runtime_config = cast("dict[str, object]", resolve_runtime_config(merged_config, variables))

    derived_variant = derive_variant(adapter_type, runtime_config)
    effective_execution_profile = (
        target.execution_profile
        or step_def.execution_profile
        or variant_override
        or derived_variant
    )
    effective_variant = (
        target.artifact_namespace
        or step_def.artifact_namespace
        or step_def.variant
        or variant_override
        or derived_variant
    )
    step_vars = dict(variables)
    step_vars["EXECUTION_PROFILE"] = effective_execution_profile
    step_vars["ARTIFACT_NAMESPACE"] = effective_variant
    step_vars["VARIANT"] = effective_variant

    effective_outputs = target.outputs
    effective_batch_size = resolve_batch_size(step_def)

    # Reconcile stale running items
    stale_count = reconcile_stale_running(run_dir)
    if stale_count:
        out.progress(f"  Reconciled {stale_count} stale running item(s)")

    # Fan-out source is resolved at plan-build time from for_each.over.
    resolved_source = target.fan_out.source
    if not resolved_source:
        raise CLIError(f"step '{step_id}' has for_each but no resolved source")

    source_path = Path(resolved_source)
    if not source_path.exists():
        raise CLIError(f"source file not found: {source_path}")

    discovery = discover_items_from_source(
        source_path,
        step_def,
        output_paths=effective_outputs or None,
        params=step_vars,
        reuse_policy=target.reuse_policy,
        run_dir=run_dir,
    )
    item_contexts = discovery.actionable_contexts
    if not item_contexts:
        out.progress(f"  Step '{step_id}': no actionable items (all completed)")
        return True

    try:
        for item_context in item_contexts:
            item_vars = dict(step_vars)
            item_vars.update(item_context)
            validate_step_inputs_exist(
                target.inputs,
                item_vars,
                context=f"step '{step_id}' for {each}={item_context[each]}",
            )
    except ValueError as exc:
        raise CLIError(str(exc)) from exc

    out.progress(
        f"  Step '{step_id}': {len(item_contexts)} actionable items "
        f"({len(discovery.filtered_items)} filtered)"
    )

    # ── gcp-worker dispatch path ─────────────────────────────────
    if backend_name == "gcp-worker":
        return await _execute_gcp_worker_dispatch(
            item_contexts=item_contexts,
            each=each,
            step_id=step_id,
            process_path=process_path,
            variables=step_vars,
            run_dir=run_dir,
            num_workers=num_workers,
            max_concurrency=max_concurrency,
            initial_concurrency=initial_concurrency,
            machine_type=machine_type,
            spot=spot,
            variant=effective_variant,
            adapter_config=merged_config,
            max_retries=(
                cast(
                    "int | None",
                    cast("dict[str, object]", merged.get("retry", {})).get("max_retries"),
                )
                if isinstance(merged.get("retry"), dict)
                else None
            ),
            auth_flags=auth_flags,
            out=out,
        )

    # Deferred: metaproc.commands.run_parallel imports metaproc.cli, which
    # imports this module — keep these local to break the cycle.
    from metaproc.commands.run_parallel import (  # noqa: PLC0415 -- circular-import guard
        _resolve_retry_policy,
        _run_agent_pool,
    )

    retry_policy = _resolve_retry_policy(step_def, spec.defaults)

    # Check if adapter needs GCP token
    _needs_gcloud_token = (
        adapter_type == "pi-cli"
        and str(merged_config.get("provider", "")).startswith("vertex")
        and backend_name == "local"
    )

    def _refresh_gcloud_token() -> None:
        if not _needs_gcloud_token:
            return

        try:
            merged_config["api_key"] = resolve_gcp_token()
        except Exception:
            merged_config.pop("api_key", None)
            raise

    # Pre-flight
    preflight_results = run_preflight(needs_gcloud=_needs_gcloud_token)
    failures = [msg for ok, msg in preflight_results if not ok]
    if failures:
        raise CLIError(f"Pre-flight check failed: {'; '.join(failures)}")

    # Cloud runs dir
    # RUNS_DIR = <mount_path>/runs (runs/ subdirectory, not share root).
    cloud_runs_dir = ""
    if backend_name != "local":
        _fs_server = MetaprocEnv.METAPROC_GCP_FILESTORE_SERVER.read_str(default="")
        if _fs_server:
            _fs_mount = MetaprocEnv.METAPROC_GCP_FILESTORE_MOUNT_PATH.read_str(
                default="/mnt/filestore"
            )
            cloud_runs_dir = _fs_mount + "/runs"

    allowed_runtime = collect_step_runtime_placeholders(step_def)
    optional_unset = {name for name in spec.optional_input_names if name not in step_vars}

    resolved_backend = get_backend(backend_name)
    # Per-pool isolation: each step's runner pool writes status and event
    # logs under its own step-scoped directories so concurrent pools never
    # clobber each other.
    step_state_dir_path = paths_mod.step_state_dir(run_dir, step_id)
    step_logs_dir_path = paths_mod.step_logs_dir(run_dir, step_id)
    boundary_before = capture_repo_snapshot(process_dir, observation_zone=[run_dir])
    allowed_targets = _dedupe_write_targets(
        collect_write_targets(
            step_def,
            effective_outputs,
            step_vars,
            step_scope=True,
        )
        + list(peer_allowed_targets or [])
    )
    ignored_targets = _dedupe_write_targets(
        [
            WriteTarget(step_state_dir_path, "tree"),
            WriteTarget(step_logs_dir_path, "tree"),
            WriteTarget(compute_logs_dir(spec, step_vars), "tree"),
            WriteTarget(run_dir / LOGS_DIR, "tree"),
        ]
    )
    # Per-variant concurrency cap: adapters can declare max_concurrency in
    # their config (e.g. pi-glm-5.config.max_concurrency: 15) to set a
    # model-specific ceiling.  Takes the min of global and variant limits.
    effective_max = max_concurrency or effective_batch_size
    resource_config = target.resources or runtime_config
    adapter_max_int: int | None = None
    adapter_max = resource_config.get("max_concurrency_hint") or runtime_config.get(
        "max_concurrency"
    )
    if adapter_max is not None:
        adapter_max_int = int(str(adapter_max))
        effective_max = min(effective_max, adapter_max_int)
        out.progress(
            f"  Adapter max_concurrency={adapter_max_int} "
            f"(effective={effective_max} after global cap)"
        )

    pool_config = RunPoolConfig(
        max_concurrency=effective_max,
        initial_concurrency=initial_concurrency or 0,
        min_concurrency=1,
        estimated_process_rss_bytes=resolve_estimated_process_rss_bytes(resource_config),
        initial_memory_budget_fraction=resolve_initial_memory_budget_fraction(resource_config),
        state_dir=step_state_dir_path,
        logs_dir=step_logs_dir_path,
        external_semaphore=external_semaphore,
        host_admission_enabled=backend_name == "local",
        host_admission_limit=resolve_host_max_concurrency(resource_config, default=effective_max),
        execution_profile=effective_execution_profile,
        cli_max_concurrency=max_concurrency,
        batch_size=effective_batch_size,
        profile_max_concurrency_hint=adapter_max_int,
    )

    if events is not None:
        _emit_fan_out_item_starts(
            events,
            step_id=step_id,
            each=each,
            item_contexts=item_contexts,
        )

    pool_dispatch = (
        dataclasses.replace(pool_dispatch_template, step=step_id)
        if pool_dispatch_template is not None
        else None
    )

    all_results = await _run_agent_pool(
        spec=spec,
        step_def=step_def,
        step=step_id,
        each=each,
        variables=step_vars,
        item_contexts=item_contexts,
        adapter_type=adapter_type,
        merged_config=merged_config,
        effective_outputs=effective_outputs,
        effective_variant=effective_variant,
        allowed_runtime=allowed_runtime | optional_unset,
        retry_policy=retry_policy,
        process_dir=process_dir,
        prompt_paths=target.prompt_paths,
        target_env=target.env,
        refresh_token_fn=_refresh_gcloud_token,
        pool_config=pool_config,
        backend=resolved_backend,
        out=out,
        cloud_runs_dir=cloud_runs_dir,
        pool_dispatch=pool_dispatch,
        preflight_quota_guard=preflight_quota_guard,
    )

    if events is not None:
        _emit_fan_out_item_results(events, step_id=step_id, results=all_results)

    failed_count = sum(1 for _, rc in all_results if rc != 0)
    succeeded = sum(1 for _, rc in all_results if rc == 0)
    if failed_count == 0 and boundary_before is not None and allowed_targets:
        boundary_after = capture_repo_snapshot(process_dir, observation_zone=[run_dir])
        violations = filter_boundary_violations(
            repo_changes_since(boundary_before, boundary_after),
            allowed=allowed_targets,
            ignored=ignored_targets,
            base_dir=boundary_after.repo_root if boundary_after else None,
        )
        if violations:
            error = (
                "write boundary violated: "
                f"{_format_boundary_paths(violations, base_dir=boundary_after.repo_root if boundary_after else None)}"
            )
            for item_context in item_contexts:
                item_vars = dict(step_vars)
                item_vars.update(item_context)
                item_state_dir = compute_task_state_dir(run_dir, step_def, item_vars)
                if read_status_at(item_state_dir) is None:
                    continue
                mark_failed_at(item_state_dir, error=error)
            out.progress(f"  Step '{step_id}': {error}")
            return False
    out.progress(f"  Step '{step_id}': {succeeded} succeeded, {failed_count} failed")
    return failed_count == 0


async def _execute_gcp_worker_dispatch(
    *,
    item_contexts: list[dict[str, str]],
    each: str,
    step_id: str,
    process_path: Path,
    variables: dict[str, str],
    run_dir: Path,
    num_workers: int,
    max_concurrency: int | None,
    initial_concurrency: int | None = None,
    machine_type: str,
    spot: bool,
    variant: str,
    adapter_config: dict[str, object],
    max_retries: int | None,
    out: Any,
    auth_flags: AuthPoolFlags = AuthPoolFlags(),
) -> bool:
    """Dispatch a fan-out step to GCP worker VMs via worker_dispatch.

    ``auth_flags`` is the resolved auth-pool dispatch config from the
    top-level CLI. Threaded explicitly so hybrid (no env-var leg) works
    the same as full-cloud (env-var leg via the orchestrator container).
    Defaults to an empty :class:`AuthPoolFlags` for legacy callers /
    tests that don't construct one — matches the worker_dispatch
    config-field default and the no-auth-pool dispatch shape.
    """

    gcp_config = build_gcp_config_from_env(machine_type=machine_type, spot=spot)

    adapter_config_json = json.dumps(adapter_config) if adapter_config else ""

    dispatch_config = WorkerDispatchConfig(
        gcp=gcp_config,
        num_workers=num_workers,
        **({"max_concurrency": max_concurrency} if max_concurrency is not None else {}),
        **({"initial_concurrency": initial_concurrency} if initial_concurrency is not None else {}),
        max_retries=max_retries,
        spot=spot,
        variant=variant,
        adapter_config_json=adapter_config_json,
        auth_flags=auth_flags,
    )

    resolved = process_path.resolve()
    repo_root = Path.cwd().resolve()
    for parent in [repo_root, *repo_root.parents]:
        if (parent / ".git").exists():
            repo_root = parent
            break
    try:
        process_spec_rel = str(resolved.relative_to(repo_root))
    except ValueError:
        process_spec_rel = str(resolved)

    # If running on an NFS-backed filesystem (e.g. Filestore), pass the run
    # dir so the poll loop can read worker pool status for live progress.
    nfs_run_dir = str(run_dir) if gcp_config.filestore_server else ""

    reconciled = await reconcile_dispatched_workers(
        run_dir=run_dir,
        step=step_id,
        item_contexts=item_contexts,
        each=each,
        config=dispatch_config,
        process_spec_rel=process_spec_rel,
        variables=variables,
        out=out,
        nfs_run_dir=nfs_run_dir,
    )

    if reconciled is not None:
        results = reconciled
    else:
        results = await dispatch_to_workers(
            item_contexts=item_contexts,
            each=each,
            config=dispatch_config,
            step=step_id,
            process_spec_rel=process_spec_rel,
            variables=variables,
            out=out,
            nfs_run_dir=nfs_run_dir,
            run_dir=run_dir,
        )

    failed_count = sum(1 for r in results if r.exit_code != 0)
    succeeded = sum(1 for r in results if r.exit_code == 0)
    msg = f"  Step '{step_id}': {succeeded} worker(s) succeeded, {failed_count} failed"
    for r in results:
        if r.error_detail:
            msg += f"\n    Worker {r.worker_id}: {r.error_detail}"
    out.progress(msg)
    return failed_count == 0


async def _execute_manual_step(
    *,
    step_def: ProcessStep,
    target: ResolvedStep,
    variables: dict[str, str],
    process_path: Path,
    run_dir: Path,
    run_id: str,
    out: Any,
) -> bool:
    """Wait for a manual-step acknowledgment artifact, then validate outputs."""
    step_id = target.step_id
    step_hash = fingerprint_step(target)

    try:
        validate_step_inputs_exist(target.inputs, variables, context=f"step '{step_id}'")
    except ValueError as exc:
        raise CLIError(str(exc)) from exc

    state_dir = compute_task_state_dir(run_dir, step_def, variables)
    artifact_dir = compute_item_dir(target.outputs, variables)
    state_dir.mkdir(parents=True, exist_ok=True)
    item_record = {"step": step_id}

    current_status = read_status_at(state_dir)
    running_record = (
        current_status if current_status and current_status.state == "running" else None
    )
    if running_record is None and (current_status is None or current_status.state != "completed"):
        running_record = mark_running_at(
            state_dir, run_id=run_id, step_id=step_id, item=item_record
        )

    ack_record = read_manual_ack_at(state_dir)
    if ack_record is None:
        out.progress(
            f"  Step '{step_id}': waiting for operator acknowledgment "
            f"({state_dir / MANUAL_ACK_FILE})"
        )
        out.progress(
            "  Acknowledge with the same process vars, for example: "
            f'python -m metaproc run-step {process_path} --step {step_id} --operator "$USER"'
        )
        poll_started = time.monotonic()
        last_heartbeat = poll_started
        while ack_record is None:
            elapsed = time.monotonic() - poll_started
            if elapsed > MANUAL_ACK_TIMEOUT_S:
                raise CLIError(
                    f"step '{step_id}': manual acknowledgment not received within "
                    f"{MANUAL_ACK_TIMEOUT_S / 3600:.0f}h "
                    f"({state_dir / MANUAL_ACK_FILE})"
                )
            if time.monotonic() - last_heartbeat > MANUAL_ACK_HEARTBEAT_S:
                out.progress(
                    f"  Step '{step_id}': still waiting for operator ack "
                    f"(elapsed {elapsed / 60:.0f}m of {MANUAL_ACK_TIMEOUT_S / 3600:.0f}h ceiling)"
                )
                last_heartbeat = time.monotonic()
            await asyncio.sleep(1.0)
            ack_record = read_manual_ack_at(state_dir)

    if target.outputs and artifact_dir is not None:
        output_failures = validate_item_outputs_detailed(
            artifact_dir, target.outputs, variables=variables
        )
        if output_failures:
            output_errors = [f.summary() for f in output_failures]
            mark_failed_at(
                state_dir,
                error=f"output validation failed: {'; '.join(output_errors)}",
                running_record=running_record,
                output_failures=output_failures,
            )
            return False

    latest_status = read_status_at(state_dir)
    if latest_status is None or latest_status.state != "completed":
        completed = mark_completed_at(state_dir, running_record=running_record)
    else:
        completed = latest_status

    write_result_at(
        state_dir,
        ResultRecord(
            run_id=run_id,
            step_id=step_id,
            state="completed",
            validated=True,
            outputs=resolve_record_output_paths(target.outputs, variables),
            published_at=completed.completed_at or _now_iso(),
            step_hash=step_hash,
        ),
    )
    out.progress(
        f"  Step '{step_id}': acknowledged by {ack_record.operator} at {ack_record.acknowledged_at}"
    )
    return True


async def _execute_step(
    *,
    spec: ProcessSpec,
    step_map: dict[str, ResolvedStep] | None = None,
    chains_by_member: dict[str, list[str]] | None = None,
    step_def: ProcessStep,
    target: ResolvedStep,
    variables: dict[str, str],
    process_path: Path,
    process_dir: Path,
    run_dir: Path,
    run_id: str,
    backend_name: str,
    max_concurrency: int | None,
    initial_concurrency: int | None = None,
    num_workers: int,
    machine_type: str,
    spot: bool,
    variant_override: str | None,
    profile_files: Sequence[Path] = (),
    external_semaphore: asyncio.Semaphore | None = None,
    peer_allowed_targets: list[WriteTarget] | None = None,
    events: ProcessEventLogger | None = None,
    out: Any,
    pool_dispatch_template: PoolDispatchConfig | None = None,
    auth_flags: AuthPoolFlags = AuthPoolFlags(),
    preflight_quota_guard: str = "warn",
    continue_on_step_failure: bool = False,
) -> bool:
    """Dispatch a step to the correct execution path. Returns True on success."""
    step_id = target.step_id

    # Fan-in bindings are materialized from durable per-item state just before the
    # consumer runs, so the collection describes what actually happened rather than
    # what was true when the plan was built.
    for input_name, io_spec in target.inputs.items():
        if not io_spec.collect or not io_spec.path:
            continue
        # The expected roster comes from the collected step's own resolved fan-out, so
        # an item that died before reaching it is still reported rather than absent.
        collected_step = (step_map or {}).get(io_spec.collect)
        expected_keys = None
        if collected_step is not None and collected_step.fan_out is not None:
            bind = collected_step.fan_out.bind
            expected_keys = [
                str(item[bind]) for item in collected_step.fan_out.items if bind in item
            ]
        # The chain feeding the collected step, so an item that never arrived can be
        # reported with where it stopped rather than as a bare absence.
        upstream_chain: list[str] = []
        for candidate in (chains_by_member or {}).get(io_spec.collect, []):
            upstream_chain.append(candidate)
            if candidate == io_spec.collect:
                break
        manifest = write_outcome_manifest(
            run_dir,
            io_spec.collect,
            Path(resolve_templates(io_spec.path, variables)),
            expected_keys,
            upstream_chain,
        )
        counts = manifest["fan_in_outcomes"]
        out.progress(
            f"  Collected '{input_name}' from '{io_spec.collect}': "
            f"{counts['succeeded']} succeeded, {counts['failed']} failed "
            f"of {counts['total']}"
        )

    if target.mode == "composite":
        return await _execute_composite_step(
            step_def=step_def,
            target=target,
            variables=variables,
            process_dir=process_dir,
            run_dir=run_dir,
            run_id=run_id,
            backend_name=backend_name,
            max_concurrency=max_concurrency,
            initial_concurrency=initial_concurrency,
            num_workers=num_workers,
            machine_type=machine_type,
            spot=spot,
            variant_override=variant_override,
            profile_files=profile_files,
            external_semaphore=external_semaphore,
            out=out,
            pool_dispatch_template=pool_dispatch_template,
            auth_flags=auth_flags,
            preflight_quota_guard=preflight_quota_guard,
            continue_on_step_failure=continue_on_step_failure,
        )

    if target.mode == "manual":
        return await _execute_manual_step(
            step_def=step_def,
            target=target,
            variables=variables,
            process_path=process_path,
            run_dir=run_dir,
            run_id=run_id,
            out=out,
        )

    if target.mode == "code":
        if target.fan_out is not None:
            return await _execute_code_fan_out_step(
                spec=spec,
                step_def=step_def,
                target=target,
                variables=variables,
                process_dir=process_dir,
                run_dir=run_dir,
                run_id=run_id,
                max_concurrency=max_concurrency,
                external_semaphore=external_semaphore,
                out=out,
            )
        return await _execute_code_step(
            spec=spec,
            step_def=step_def,
            target=target,
            variables=variables,
            process_dir=process_dir,
            run_dir=run_dir,
            run_id=run_id,
            out=out,
        )

    if target.mode == "agent":
        if target.fan_out is not None:
            return await _execute_fan_out_step(
                spec=spec,
                step_def=step_def,
                target=target,
                variables=variables,
                process_path=process_path,
                process_dir=process_dir,
                run_dir=run_dir,
                run_id=run_id,
                backend_name=backend_name,
                max_concurrency=max_concurrency,
                initial_concurrency=initial_concurrency,
                num_workers=num_workers,
                machine_type=machine_type,
                spot=spot,
                variant_override=variant_override,
                external_semaphore=external_semaphore,
                peer_allowed_targets=peer_allowed_targets,
                events=events,
                out=out,
                pool_dispatch_template=pool_dispatch_template,
                auth_flags=auth_flags,
                preflight_quota_guard=preflight_quota_guard,
            )
        return await _execute_agent_step(
            spec=spec,
            step_def=step_def,
            target=target,
            variables=variables,
            process_dir=process_dir,
            run_dir=run_dir,
            run_id=run_id,
            variant_override=variant_override,
            peer_allowed_targets=peer_allowed_targets,
            backend_name=backend_name,
            out=out,
        )

    raise CLIError(f"step '{step_id}': unsupported mode '{target.mode}'")


# ── Orchestration loop ────────────────────────────────────────────


async def _orchestrate(
    *,
    spec: ProcessSpec,
    plan: Plan,
    variables: dict[str, str],
    process_path: Path,
    process_dir: Path,
    run_dir: Path,
    run_id: str,
    backend_name: str,
    max_concurrency: int | None,
    initial_concurrency: int | None = None,
    num_workers: int,
    machine_type: str,
    spot: bool,
    variant_override: str | None,
    skip_steps: set[str],
    force: bool,
    continue_on_error: bool,
    continue_on_step_failure: bool = False,
    out: Any,
    events: ProcessEventLogger,
    pool_dispatch_template: PoolDispatchConfig | None = None,
    auth_flags: AuthPoolFlags = AuthPoolFlags(),
    preflight_quota_guard: str = "warn",
    profile_files: Sequence[Path] = (),
) -> None:
    """Walk the DAG, executing independent steps in parallel via asyncio.gather()."""
    started_at = _now_iso()
    step_map = {s.step_id: s for s in plan.steps}
    step_def_map = {s.id: s for s in spec.steps}
    levels = topo_sort(plan.steps)

    # Item-aligned chains run per item under their head, so the level walk must not
    # also run the absorbed members: their edges are item-scoped and the barrier
    # between them is exactly what alignment removes. Chains of code steps only, since
    # the agent path carries dispatch machinery this executor does not reproduce.
    _chains = [
        chain
        for chain in item_aligned_chains(plan.steps)
        if all(step_map[sid].mode == "code" for sid in chain)
    ]
    _chain_head_of: dict[str, list[str]] = {chain[0]: chain for chain in _chains}
    _chains_by_member: dict[str, list[str]] = {
        member: chain for chain in _chains for member in chain
    }
    _absorbed: set[str] = {sid for chain in _chains for sid in chain[1:]}

    events.process_start(spec.name, run_id, backend_name, len(step_map))

    # Shared semaphore for global fan-out concurrency cap (RF-4).
    # When max_concurrency is set, it bounds the total concurrent items
    # across all parallel fan-out steps at the same topological level.
    global_semaphore: asyncio.Semaphore | None = None
    if max_concurrency is not None:
        global_semaphore = asyncio.Semaphore(max_concurrency)

    # Initialize step states for process-status.yaml. For partial resumes
    # (``--from``/``--only``), preserve any prior completion records from a
    # previous write — otherwise the next run's process-status.yaml would
    # forget upstream steps' completion state, and downstream
    # `_is_step_completed` checks that use process-status.yaml as a fallback
    # (for fan-out ancestors) would falsely report "no completion record"
    # on subsequent resumes.
    prior_ps = _read_process_status_yaml(run_dir)
    prior_steps: dict[str, Any] = {}
    if isinstance(prior_ps, dict):
        raw = prior_ps.get("steps")
        if isinstance(raw, dict):
            prior_steps = raw

    step_states: dict[str, dict[str, Any]] = {}
    active_ids: set[str] = set()
    for level in levels:
        for step_id in level:
            step_states[step_id] = {"state": "pending"}
            active_ids.add(step_id)

    # Carry over prior states for steps NOT in this run's active set so the
    # authoritative process-status view stays coherent across partial runs.
    for step_id, prior in prior_steps.items():
        if step_id not in active_ids and isinstance(prior, dict):
            step_states[step_id] = dict(prior)

    blocked_steps: set[str] = set()

    async def _run_one_step(
        step_id: str,
        *,
        peer_allowed_targets: list[WriteTarget] | None = None,
    ) -> tuple[str, bool]:
        """Execute a single step and return (step_id, success)."""
        target = step_map[step_id]

        step_states[step_id] = {"state": "running", "started_at": _now_iso()}
        _write_process_status(run_dir, spec.name, step_states, started_at)

        step_start = time.monotonic()
        out.progress(f"\n--- Step: {step_id} ({target.mode}) ---")
        events.step_start(step_id, target.mode)

        step_def = step_def_map[step_id]

        chain = _chain_head_of.get(step_id)
        if chain is not None:
            chain_results = await _execute_item_aligned_chain(
                spec=spec,
                chain=chain,
                step_map=step_map,
                step_def_map=step_def_map,
                variables=variables,
                process_dir=process_dir,
                run_dir=run_dir,
                run_id=run_id,
                max_concurrency=max_concurrency,
                external_semaphore=global_semaphore,
                out=out,
            )
            chain_elapsed = time.monotonic() - step_start
            for member_id, member_ok in chain_results.items():
                step_states[member_id] = (
                    {"state": "completed", "completed_at": _now_iso()}
                    if member_ok
                    else {"state": "failed", "completed_at": _now_iso()}
                )
                if member_id != step_id:
                    events.step_start(member_id, step_map[member_id].mode)
                if member_ok:
                    events.step_complete(member_id, chain_elapsed)
                else:
                    events.step_fail(member_id, chain_elapsed)
            _write_process_status(run_dir, spec.name, step_states, started_at)
            failed_members = [m for m, ok in chain_results.items() if not ok]
            for member_id in failed_members:
                out.progress(f"  Step '{member_id}': FAILED")
            return step_id, not failed_members

        success = await _execute_step(
            spec=spec,
            step_map=step_map,
            chains_by_member=_chains_by_member,
            step_def=step_def,
            target=target,
            variables=variables,
            process_path=process_path,
            process_dir=process_dir,
            run_dir=run_dir,
            run_id=run_id,
            backend_name=backend_name,
            max_concurrency=max_concurrency,
            initial_concurrency=initial_concurrency,
            num_workers=num_workers,
            machine_type=machine_type,
            spot=spot,
            variant_override=variant_override,
            profile_files=profile_files,
            external_semaphore=global_semaphore,
            peer_allowed_targets=peer_allowed_targets,
            events=events,
            out=out,
            pool_dispatch_template=pool_dispatch_template,
            auth_flags=auth_flags,
            preflight_quota_guard=preflight_quota_guard,
            continue_on_step_failure=continue_on_step_failure,
        )

        elapsed = time.monotonic() - step_start
        if success:
            step_states[step_id] = {
                "state": "completed",
                "started_at": step_states[step_id].get("started_at"),
                "completed_at": _now_iso(),
                "elapsed_s": round(elapsed, 1),
                "recorded_step_hash": fingerprint_step(target),
            }
            events.step_complete(step_id, elapsed)
            out.progress(f"  Step '{step_id}': completed ({fmt_timedelta(elapsed)})")
        else:
            step_states[step_id] = {
                "state": "failed",
                "started_at": step_states[step_id].get("started_at"),
                "completed_at": _now_iso(),
                "elapsed_s": round(elapsed, 1),
            }
            events.step_fail(step_id, elapsed)
            out.progress(f"  Step '{step_id}': FAILED ({fmt_timedelta(elapsed)})")

        return step_id, success

    for _level_idx, level in enumerate(levels):
        events.level_start(_level_idx, list(level))

        # Read overrides once per level so a satisfied-universally entry skips
        # the step on bare resume (without --from). Without this, a step with
        # state=failed but a `metaproc override <step> --satisfied` entry was
        # re-run from scratch on whole-DAG resume — the override only fired
        # on `--from <step>` resumes via _verify_ancestors. Friday 2026-05-01
        # cohort: bare resume cleaned adhoc-kb-candidates and rebuilt mine-adhoc
        # despite an earlier `override mine-adhoc --satisfied`.
        level_overrides_doc = _read_overrides_for_verify(run_dir)

        # Filter steps: skip blocked, user-skipped, override-satisfied, and already-completed.
        runnable: list[str] = []
        for step_id in level:
            if step_id in _absorbed:
                # Run by the head of its item-aligned chain, at the head's level.
                continue
            if step_id in blocked_steps:
                step_states[step_id] = {"state": "blocked"}
                events.step_blocked(step_id)
                out.progress(f"  Step '{step_id}': blocked (upstream failure)")
                continue
            if step_id in skip_steps:
                step_states[step_id] = {"state": "skipped"}
                events.step_skip(step_id, "--skip")
                out.progress(f"  Step '{step_id}': skipped (--skip)")
                continue
            if (
                not force
                and level_overrides_doc is not None
                and is_step_satisfied_universally(level_overrides_doc, step_id)
            ):
                ov_entries = overrides_for_step(level_overrides_doc, step_id)
                ov_note = ov_entries[0].note if ov_entries else ""
                step_states[step_id] = {
                    "state": "completed",
                    "note": f"satisfied via override: {ov_note}",
                }
                events.step_skip(step_id, "satisfied via override")
                out.progress(f"  Step '{step_id}': satisfied via override — skipping")
                continue
            target = step_map[step_id]
            # The level walk reaches an item-aligned chain only through its head. Even
            # when the head is complete, the chain may contain incomplete tasks; the
            # chain executor's per-task checks decide what resume reuses.
            if (
                not force
                and step_id not in _chain_head_of
                and _is_step_completed(
                    run_dir,
                    step_id,
                    outputs=target.outputs,
                    variables=variables,
                    is_fan_out=target.fan_out is not None,
                    step=target,
                )
            ):
                completed_entry: dict[str, Any] = {
                    "state": "completed",
                    "note": "previously completed",
                }
                # Carry forward the recorded fingerprint so a future resume
                # against this same RUN_ID can still detect a step-definition
                # change without having to scan per-task result.yaml files.
                prior = prior_steps.get(step_id)
                if isinstance(prior, dict):
                    prior_hash = prior.get("recorded_step_hash")
                    if isinstance(prior_hash, str) and prior_hash:
                        completed_entry["recorded_step_hash"] = prior_hash
                step_states[step_id] = completed_entry
                events.step_skip(step_id, "previously completed")
                out.progress(f"  Step '{step_id}': already completed — skipping")
                continue
            if force:
                invalidated = _invalidate_downstream(
                    run_dir,
                    step_id,
                    plan,
                    variables=variables,
                )
                if invalidated:
                    out.progress(f"  Invalidated downstream: {', '.join(invalidated)}")
            else:
                # Edit-and-rerun cascade: if the step's fingerprint changed
                # since the prior completion, rename downstream per-task
                # status.yamls to .stale so descendants re-execute too.
                _maybe_cascade_for_fingerprint(
                    run_dir,
                    plan,
                    target,
                    variables=variables,
                    out=out,
                )
            runnable.append(step_id)

        if not runnable:
            events.level_complete(_level_idx, 0.0)
            continue

        peer_allowed_by_step = {
            step_id: _dedupe_write_targets(
                [
                    target
                    for other_id in runnable
                    if other_id != step_id
                    for target in _step_concurrency_targets(
                        target=step_map[other_id],
                        step_def=step_def_map[other_id],
                        variables=variables,
                        run_dir=run_dir,
                    )
                ]
            )
            for step_id in runnable
        }

        # Execute steps at this level in parallel.
        level_start_t = time.monotonic()
        results = await asyncio.gather(
            *[
                _run_one_step(
                    sid,
                    peer_allowed_targets=peer_allowed_by_step[sid],
                )
                for sid in runnable
            ]
        )
        events.level_complete(_level_idx, time.monotonic() - level_start_t)

        # Process results: block downstream of failed steps. Steps marked
        # `on_failure: continue` (e.g. end-of-run rollup / run-stats) are
        # excluded — they run regardless of upstream failures.
        for step_id, success in results:
            if not success:
                actually_blocked = propagate_failure(plan.steps, step_id)
                blocked_steps.update(actually_blocked)

                if not continue_on_error:
                    _write_process_status(run_dir, spec.name, step_states, started_at)
                    raise CLIError(
                        f"Step '{step_id}' failed (--no-continue-on-error set). "
                        f"Blocked: {', '.join(actually_blocked) if actually_blocked else 'none'}"
                    )

            _write_process_status(run_dir, spec.name, step_states, started_at)

    # Final status
    _write_process_status(run_dir, spec.name, step_states, started_at)

    completed_count = sum(1 for s in step_states.values() if s.get("state") == "completed")
    failed_steps = [sid for sid, s in step_states.items() if s.get("state") == "failed"]
    skipped_count = sum(1 for s in step_states.values() if s.get("state") == "skipped")
    blocked_list = [sid for sid, s in step_states.items() if s.get("state") == "blocked"]
    total_elapsed = sum(s.get("elapsed_s", 0) for s in step_states.values())

    events.process_complete(
        spec.name,
        run_id,
        completed=completed_count,
        failed=len(failed_steps),
        skipped=skipped_count + len(blocked_list),
        elapsed_s=total_elapsed,
    )

    if failed_steps:
        raise CLIError(
            f"Process completed with failures: {', '.join(failed_steps)}. "
            f"Blocked: {', '.join(blocked_list) if blocked_list else 'none'}"
        )


# ── CLI command ───────────────────────────────────────────────────


@app.command("run-process")
def run_process_command(
    process_spec: Path = typer.Argument(..., help="Path to .process.md spec file"),
    var: list[str] = typer.Option([], "--var", help="KEY=VALUE parameters (repeatable)"),
    from_step: str | None = typer.Option(  # noqa: UP007
        None, "--from", help="Start from this step; ancestors must already be completed"
    ),
    only_step: str | None = typer.Option(  # noqa: UP007
        None, "--only", help="Run only this single step (no downstream)"
    ),
    skip: list[str] = typer.Option([], "--skip", help="Skip specific steps (repeatable)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show execution plan without running"),
    backend: str = typer.Option(
        "local", "--backend", help="Launch backend for fan-out steps (local, gcp-worker)"
    ),
    max_concurrency: int | None = typer.Option(  # noqa: UP007
        None,
        "--max-concurrency",
        help=(
            "Per-pool concurrency limit for fan-out steps. "
            "Falls back to METAPROC_DEFAULT_MAX_CONCURRENCY env var when unset "
            "(then to no cap). Primary quota-safety knob — change when quotas change."
        ),
    ),
    initial_concurrency: int | None = typer.Option(  # noqa: UP007
        None,
        "--initial-concurrency",
        help=(
            "Starting concurrency for fan-out runpools; ramps up to max via "
            "memory-pressure checks. Defaults to auto-estimation when unset."
        ),
    ),
    num_workers: int | None = typer.Option(  # noqa: UP007
        None,
        "--num-workers",
        help=(
            "Number of worker VMs for gcp-worker backend. "
            "Falls back to METAPROC_DEFAULT_NUM_WORKERS env var when unset "
            "(then to 1). Total concurrency is num_workers * max_concurrency."
        ),
    ),
    machine_type: str = typer.Option(
        "",
        "--machine-type",
        help="GCP machine type for gcp-worker backend (default: from env or n2-highmem-8)",
    ),
    spot: bool = typer.Option(True, "--spot/--no-spot", help="Use Spot VMs for gcp-worker backend"),
    variant: str | None = typer.Option(  # noqa: UP007
        None, "--variant", help="Override adapter/variant for fan-out steps (e.g. pi-deepseek-v3.2)"
    ),
    artifact_namespace: str | None = typer.Option(  # noqa: UP007
        None,
        "--artifact-namespace",
        help="Artifact namespace for output grouping; defaults to --variant when set.",
    ),
    variant_file: list[Path] = typer.Option(
        [],
        "--variant-file",
        help="Additional execution-profile registry file (repeatable).",
    ),
    profile_file: list[Path] = typer.Option(
        [],
        "--profile-file",
        help="Additional execution-profile registry file (repeatable).",
    ),
    step_variant: list[str] = typer.Option(
        [],
        "--step-variant",
        help="STEP=PROFILE execution-profile override for a single step (repeatable).",
    ),
    adapter_config: list[str] = typer.Option(
        [], "--adapter-config", help="KEY=VALUE adapter config overrides (repeatable)"
    ),
    force: bool = typer.Option(False, "--force", help="Re-run steps even if already completed"),
    continue_on_error: bool = typer.Option(
        True,
        "--continue-on-error/--no-continue-on-error",
        help=(
            "Continue executing independent branches on per-step failure "
            "(default: True; use --no-continue-on-error to fail-fast on first "
            "step failure). Per-item isolation within fan-out steps is always on."
        ),
    ),
    continue_on_step_failure: bool = typer.Option(
        False,
        "--continue-on-step-failure",
        help=(
            "Inside a composite step, if one sub-step has "
            "a per-item permanent_failure, continue launching the NEXT sub-step "
            "for the OK items rather than aborting the whole composite. Default "
            "False (preserves current behavior). Use for multi-item fan-out "
            "where one bad item should not take out the entire downstream pipeline."
        ),
    ),
    cloud: bool = typer.Option(
        False, "--cloud", help="Submit orchestrator to GCP Batch instead of running locally"
    ),
    orchestrator_machine_type: str = typer.Option(
        "e2-standard-4",
        "--orchestrator-machine-type",
        help="GCP machine type for orchestrator VM (only with --cloud)",
    ),
    max_duration: str = typer.Option(
        "8h",
        "--max-duration",
        help="Max runtime for orchestrator Batch job (e.g. 8h, 3600s) (only with --cloud)",
    ),
    auth_account: str | None = typer.Option(  # noqa: UP007
        None,
        "--auth-account",
        help=(
            "Adapter type to lease pool credentials from (e.g. claude-code-cli). "
            "Activates auth-pool dispatch for fan-out steps; each item attempt "
            "leases an eligible label per --auth-fallback-policy."
        ),
    ),
    auth_backend: str | None = typer.Option(  # noqa: UP007
        None,
        "--auth-backend",
        help=(
            "Pool storage backend: 'local' or 'gcp-secret-manager'. "
            "Defaults to 'gcp-secret-manager' when --backend gcp-worker, "
            "else 'local'."
        ),
    ),
    auth_fallback_policy: str = typer.Option(
        "none",
        "--auth-fallback-policy",
        help=(
            "Cross-adapter fallback policy when primary exhausted: "
            "none|same-provider|cross-provider|both. "
            "same-provider walks other labels on the same adapter when "
            "primary all-cooling; default 'none' returns None on exhaustion."
        ),
    ),
    auth_policy: str | None = typer.Option(  # noqa: UP007 — typer-friendly Optional
        None,
        "--auth-policy",
        help=(
            "Label selection policy: priority-order|round-robin|least-active. "
            "Defaults to round-robin when --auth-include-labels has ≥ 2 "
            "labels (plan-2026-05-03 fix for P0-10), else priority-order. "
            "Pass --auth-policy priority-order explicitly to reproduce "
            "legacy first-eligible behavior bit-for-bit."
        ),
    ),
    auth_include_labels: list[str] = typer.Option(
        [],
        "--auth-include-labels",
        help=(
            "Restrict pool selection to these labels only (repeatable). "
            "Other labels for the adapter are excluded. Mutually exclusive "
            "with --auth-exclude-labels. Use to scope worker pool to a "
            "subset (e.g. --auth-include-labels alt1 --auth-include-labels alt2)."
        ),
    ),
    auth_exclude_labels: list[str] = typer.Option(
        [],
        "--auth-exclude-labels",
        help="Exclude these labels (repeatable). Mutually exclusive with --auth-include-labels.",
    ),
    auth_cross_quota_group: bool = typer.Option(
        True,
        "--auth-cross-quota-group/--no-auth-cross-quota-group",
        help=(
            "On 429 cooling, expand pool_exclude to every sibling label sharing the "
            "failing label's quota_group (organization_uuid or account_id). Default "
            "true. Pass --no-auth-cross-quota-group for diagnostic dispatches against "
            "a single account where the expansion would empty the pool."
        ),
    ),
    auth_preflight_quota_guard: str = typer.Option(
        "warn",
        "--auth-preflight-quota-guard",
        help=(
            "Pre-fan-out per-step quota gate posture: off|warn|refuse. "
            "Defaults to warn. refuse blocks dispatch when projected demand "
            "exceeds 80% of pool headroom (computed via "
            "claude_code.query_quota_usage's recent rate_limit_event "
            "synthesis); off skips the gate."
        ),
    ),
) -> None:
    """Execute a process spec DAG — all steps in dependency order."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    out = get_output()

    # Validate --auth-preflight-quota-guard at parse time; an invalid
    # value would otherwise alternate between "go" and "refuse"
    # depending on telemetry availability (upstream change review P2).
    try:
        validate_guard_posture(auth_preflight_quota_guard)
    except ValueError as exc:
        raise CLIError(str(exc)) from exc

    # Dispatch-start ambient-auth environment sweep.
    # Fires before any further setup — including --dry-run plan rendering
    # — so operators previewing an upcoming dispatch see the conflict
    # surfaced at the same point a real launch would. The per-item
    # compose_slot_env check only fires when the first matching-adapter
    # item tries to launch; a long pool-bypass step (e.g. mine-adhoc on
    # pi-cli) running first can complete before any pooled step's first
    # item ever calls compose_slot_env, so this gate eliminates the
    # 30+-minute timing window the 2026-05-10 dispatch hit.
    if auth_account:
        env_verdict = check_dispatch_auth_env(
            auth_account,
            posture=cast(GuardPosture, auth_preflight_quota_guard),
        )
        if env_verdict.status == "refuse":
            raise CLIError(env_verdict.message)
        if env_verdict.status == "warn" and env_verdict.conflicting_keys:
            out.warning(env_verdict.message)

    # Apply env-var fallbacks for quota-safety knobs. Operators should change
    # quota limits in one place (env or shell profile), not across every
    # playbook and process spec. METAPROC_DEFAULT_MAX_CONCURRENCY is the
    # single source of truth for "how many concurrent LLM calls does current
    # Vertex MaaS DSQ quota tolerate"; bump it when quotas change.
    if max_concurrency is None:
        env_max = MetaprocEnv.METAPROC_DEFAULT_MAX_CONCURRENCY.read_int(default=None)
        if env_max is not None:
            max_concurrency = env_max
    if num_workers is None:
        num_workers = MetaprocEnv.METAPROC_DEFAULT_NUM_WORKERS.read_int(default=1)

    process_path = resolve_process_path(process_spec)
    process_dir = process_path.parent

    spec = load_process_spec(process_path)
    variables = seed_runtime_vars(parse_var_args(var))
    config_overrides = parse_adapter_config(adapter_config)
    profile_files = [*variant_file, *profile_file]
    step_profile_overrides = parse_step_variant_args(step_variant)

    # Auto-generate RUN_ID if not provided and the spec requires it.
    if "RUN_ID" not in variables and spec.requires_param("RUN_ID"):
        from metaproc.engine.run_id import (  # noqa: PLC0415 -- pre-existing local import; needs review
            generate_run_id,
        )

        variables["RUN_ID"] = generate_run_id(process_slug=spec.name)
        log.info("Auto-generated RUN_ID: %s", variables["RUN_ID"])
        out.progress(f"Auto-generated RUN_ID: {variables['RUN_ID']}")

    variables = expand_process_vars(spec, variables, process_dir=process_dir)
    require_runtime_runs_dir(variables, command="run-process")

    placeholder_errors = validate_spec_placeholders(spec, variables)
    if placeholder_errors:
        msg = (
            "unresolved placeholders in process spec (pass via --var or set env var):\n  "
            + "\n  ".join(placeholder_errors)
        )
        raise CLIError(msg)

    input_errors = validate_process_inputs(spec, variables, process_dir)
    if input_errors:
        msg = "process input validation failed:\n  " + "\n  ".join(input_errors)
        raise CLIError(msg)

    try:
        plan = build_plan(
            spec,
            variables,
            process_path=process_path,
            adapter_override=variant or None,
            artifact_namespace=artifact_namespace,
            profile_files=profile_files,
            step_profile_overrides=step_profile_overrides,
            config_overrides=config_overrides or None,
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError(str(exc)) from exc

    if plan.execution_profile:
        variables["EXECUTION_PROFILE"] = plan.execution_profile
    if plan.artifact_namespace:
        variables["ARTIFACT_NAMESPACE"] = plan.artifact_namespace
        variables.setdefault("VARIANT", plan.artifact_namespace)

    # Validate step references
    all_step_ids = {s.step_id for s in plan.steps}
    for s in skip:
        if s not in all_step_ids:
            raise CLIError(f"--skip '{s}' not found. Available: {', '.join(sorted(all_step_ids))}")
    if from_step and from_step not in all_step_ids:
        raise CLIError(
            f"--from '{from_step}' not found. Available: {', '.join(sorted(all_step_ids))}"
        )
    if only_step and only_step not in all_step_ids:
        raise CLIError(
            f"--only '{only_step}' not found. Available: {', '.join(sorted(all_step_ids))}"
        )

    # Determine active step IDs (subgraph)
    active_step_ids = set(all_step_ids)
    if only_step:
        active_step_ids = {only_step}
    if from_step:
        dep_ids = downstream(plan.steps, from_step)
        active_step_ids = {from_step, *dep_ids}

    # Compute levels for only active steps
    levels = topo_sort(plan.steps, step_ids=active_step_ids)
    step_map = {s.step_id: s for s in plan.steps}
    skip_set = set(skip)

    # Surface harness-CLI version drift up front — prominently, but without
    # blocking. A drifted CLI can behave differently from the pin (a common
    # source of confusing debugging), so we warn loudly at launch rather than
    # letting it surface mid-DAG. Dry runs stay side-effect-free.
    if not dry_run:
        drift_warnings = _preflight_plan_adapters(plan, active_step_ids=active_step_ids - skip_set)
        if drift_warnings:
            out.progress("!" * 78)
            out.progress(
                "WARNING: harness CLI version drift detected (not blocking; "
                "behavior may differ from the pin):"
            )
            for message in drift_warnings:
                out.progress(f"  - {message}")
            out.progress("!" * 78)

    # ── Multi-lane execution guard ──
    # The lane planner can materialize plan.execution_lanes with more than one
    # row (operators authoring lane_matrix or passing --execution-profiles
    # via dispatch_control), but the runner does not yet drive multiple lanes
    # in a single pool — RunPoolConfig still carries one execution_profile and
    # run_parallel stamps that one profile as the lane id on every submission.
    # If a real launch went through with len(plan.execution_lanes) > 1, the
    # operator would get the dry-run preview's items×lanes count but only
    # items×1 worth of actual work, silently under the run profile. Refuse
    # the launch and direct operators at the sibling-run shape until
    # same-runpool multi-lane execution can preserve per-lane configuration.
    # Dry-run remains permissive so plan inspection works.
    if not dry_run and len(plan.execution_lanes) > 1:
        lane_ids = ", ".join(lane.lane_id for lane in plan.execution_lanes)
        raise CLIError(
            "process declares a lane_matrix with multiple lanes "
            f"({len(plan.execution_lanes)}: {lane_ids}), but same-runpool "
            "multi-lane execution is not implemented yet. The runner would "
            "launch one task per item under the single run profile, ignoring "
            "the other lanes. For multi-lane comparisons today, compile a "
            "dispatch plan with --execution-profiles (one sibling run per "
            "lane) — see "
            "docs/arch/arch-metaproc-core.md "
            "§ Phase 2 follow-up."
        )

    # ── Batch-dispatch warnings (fire for both --cloud and hybrid) ──
    # Any dispatch path that sends work to GCP Batch (orchestrator or per-step
    # worker fan-out) exercises the agent image's baked metaproc/ code unless
    # METAPROC_WHEEL_GCS is set. Surface the preflight warnings once, before
    # either branch submits a Batch job. Emit on dry-run too so operators
    # iterating invocations see the warning before they hit enter.
    if backend == "gcp-worker":
        from metaproc.engine.preflight import (  # noqa: PLC0415 -- pre-existing local import; needs review
            run_cloud_preflight_warnings,
        )

        for ok, msg in run_cloud_preflight_warnings():
            if ok:
                log.info(msg)
            else:
                out.warning(f"Cloud preflight warning: {msg}")

    # Dry-run output
    if dry_run:
        out.data(f"Process: {spec.name}")
        out.data(f"Steps: {len(active_step_ids)}")
        out.data(f"Levels: {len(levels)}")
        lane_count = len(plan.execution_lanes)
        if lane_count > 1:
            out.data(f"Execution lanes: {lane_count}")
            for lane in plan.execution_lanes:
                role = f" role={lane.comparison_role}" if lane.comparison_role else ""
                replica = f" r{lane.replica_index}" if lane.replica_index else ""
                out.data(
                    f"  - {lane.lane_id} (profile={lane.execution_profile}, "
                    f"adapter={lane.adapter}{replica}){role}"
                )
        out.data("")
        for level_idx, level in enumerate(levels):
            step_descs = []
            for step_id in level:
                target = step_map[step_id]
                mode = target.mode
                if target.fan_out is not None:
                    items = target.fan_out.items
                    if lane_count > 1:
                        mode = (
                            f"agent, fan-out: {len(items)} items × "
                            f"{lane_count} lanes = {len(items) * lane_count} tasks"
                        )
                    else:
                        mode = f"agent, fan-out: {len(items)} items"
                if step_id in skip_set:
                    mode = f"{mode} [SKIP]"
                step_descs.append(f"{step_id} ({mode})")
            parallel_note = "  [parallel]" if len(level) > 1 else ""
            out.data(f"Level {level_idx}: {', '.join(step_descs)}{parallel_note}")
        return

    # ── Cloud dispatch path ──────────────────────────────────────────
    if cloud:
        if backend != "gcp-worker":
            raise CLIError("--cloud requires --backend gcp-worker")

        from metaproc.engine.preflight import (  # noqa: PLC0415 -- pre-existing local import; needs review
            run_cloud_preflight,
        )

        cloud_results = run_cloud_preflight()
        cloud_failures = [msg for ok, msg in cloud_results if not ok]
        if cloud_failures:
            raise CLIError("Cloud preflight failed:\n  " + "\n  ".join(cloud_failures))

        from metaproc.cloud.gcp.orchestrator_dispatch import (  # noqa: PLC0415 -- pre-existing local import; needs review
            OrchestratorDispatchConfig,
            dispatch_orchestrator,
        )
        from metaproc.cloud.gcp.worker_dispatch import (  # noqa: PLC0415 -- pre-existing local import; needs review
            build_gcp_config_from_env,
        )

        gcp_config = build_gcp_config_from_env(machine_type=machine_type, spot=spot)

        # Compute process_spec_rel: relative to the git repo root so the
        # orchestrator VM can join it with /workspace/repo (the clone dir).
        # .resolve() collapses ".." that would otherwise escape the clone root.
        resolved = process_path.resolve()
        repo_root = Path.cwd().resolve()
        for parent in [repo_root, *repo_root.parents]:
            if (parent / ".git").exists():
                repo_root = parent
                break
        try:
            process_spec_rel = str(resolved.relative_to(repo_root))
        except ValueError:
            process_spec_rel = str(resolved)

        if auth_account and auth_include_labels and auth_exclude_labels:
            raise CLIError("--auth-include-labels and --auth-exclude-labels are mutually exclusive")

        orch_config = OrchestratorDispatchConfig(
            gcp=gcp_config,
            process_spec_rel=process_spec_rel,
            variables=variables,
            num_workers=num_workers,
            worker_machine_type=machine_type,
            max_concurrency=max_concurrency,
            initial_concurrency=initial_concurrency,
            spot_workers=spot,
            variant=variant or "",
            adapter_config=config_overrides or None,
            skip_steps=list(skip) if skip else None,
            from_step=from_step or "",
            only_step=only_step or "",
            force=force,
            continue_on_error=continue_on_error,
            orchestrator_machine_type=orchestrator_machine_type,
            max_duration_s=_parse_duration(max_duration),
            auth_account=auth_account,
            auth_backend=auth_backend,
            auth_fallback_policy=auth_fallback_policy,
            auth_include_labels=tuple(auth_include_labels),
            auth_exclude_labels=tuple(auth_exclude_labels),
            auth_cross_quota_group=auth_cross_quota_group,
        )

        out.progress("run-process --cloud: submitting orchestrator to GCP Batch")
        out.progress(f"  Process: {spec.name}")
        out.progress(f"  Orchestrator VM: {orchestrator_machine_type}")
        out.progress(f"  Max duration: {max_duration}")

        result = asyncio.run(dispatch_orchestrator(orch_config))

        out.progress(f"\nOrchestrator job {result.job_id}: {result.state}")
        if result.exit_code != 0:
            raise CLIError(
                f"Orchestrator job {result.job_id} {result.state} (exit code {result.exit_code})"
            )
        return

    # ── Local execution path ──────────────────────────────────────────

    # Compute run directory
    run_dir = compute_run_dir(spec, variables)
    run_dir.mkdir(parents=True, exist_ok=True)

    run_context = variables.get("RUN_ID", variables.get("DATE", variables.get("SCOPE", "")))
    run_id = f"{spec.name}/{run_context}"

    # Build an AuthPoolFlags snapshot from raw CLI args so the run-config
    # records the operator's intent. (The resolved-backend plug-in
    # happens later in the auth-pool construction; for the on-disk
    # config we want what the operator ASKED for, not the resolved
    # default — the resolved value is a derivation easily recovered.)
    auth_flags_for_config = AuthPoolFlags(
        auth_account=auth_account or "",
        auth_backend=auth_backend or "",
        auth_fallback_policy=auth_fallback_policy,
        auth_policy=auth_policy or "",
        auth_include_labels=tuple(auth_include_labels),
        auth_exclude_labels=tuple(auth_exclude_labels),
        auth_cross_quota_group=auth_cross_quota_group,
    )
    snapshot_bundle = load_plan_bundle(process_path, params=variables)
    snapshot_bundle = dataclasses.replace(snapshot_bundle, plan=plan, spec=spec)
    resource_snapshot = (
        None
        if paths_mod.run_config_file(run_dir).exists()
        else build_resource_run_snapshot(snapshot_bundle, run_id=run_id)
    )
    # Write or validate run-config.yaml (immutable run identity +
    # additive auth/concurrency capture per plan-2026-05-03 Phase 3).
    _write_run_config(
        run_dir,
        process_name=spec.name,
        process_path=process_path,
        run_id=run_context,
        variables=variables,
        backend=backend,
        variant=plan.artifact_namespace or variant,
        execution_profile=plan.execution_profile,
        artifact_namespace=plan.artifact_namespace,
        resolved_profiles=_serialize_plan_profiles(plan),
        auth_flags=auth_flags_for_config,
        max_concurrency=max_concurrency,
        resource_snapshot=resource_snapshot,
    )

    # Hybrid-mode alignment check: warn if a cloud backend is used but the
    # orchestrator's RUNS_DIR is on local disk (not Filestore). In that
    # case the orchestrator state lives locally and the run is not resumable
    # from another host. Use --cloud for durable cloud execution.
    if backend != "local":
        _fs_server = MetaprocEnv.METAPROC_GCP_FILESTORE_SERVER.read_str(default="")
        if _fs_server:
            _fs_mount = MetaprocEnv.METAPROC_GCP_FILESTORE_MOUNT_PATH.read_str(
                default="/mnt/filestore"
            )
            cloud_runs_dir = _fs_mount + "/runs"
            if not str(run_dir).startswith(cloud_runs_dir):
                log.warning(
                    "Hybrid mode: orchestrator run_dir (%s) is not on Filestore (%s). "
                    "Orchestrator state lives on local disk only. For cross-host resume, "
                    "use --cloud or set RUNS_DIR to the Filestore mount path.",
                    run_dir,
                    cloud_runs_dir,
                )
                out.progress(
                    f"  WARNING: hybrid mode — orchestrator state is local ({run_dir}), "
                    f"not on Filestore ({cloud_runs_dir}). "
                    f"Use --cloud for durable cloud execution."
                )

    out.progress(f"run-process: {spec.name} ({len(active_step_ids)} steps, {len(levels)} levels)")
    out.progress(f"Run dir: {run_dir}")
    out.progress(f"Backend: {backend}")

    # ── Auth-pool dispatch template ────────────────────
    # Constructed once at top level and threaded through to fan-out steps.
    # `step` is filled per-step inside _execute_fan_out_step via
    # dataclasses.replace before passing to _run_agent_pool.
    #
    # ``auth_flags_resolved`` is the AuthPoolFlags shape the gcp-worker
    # leg consumes. Built alongside pool_dispatch_template
    # with the worker-default backend resolved (gcp-secret-manager for
    # cloud workers) so the worker run-parallel receives an explicit
    # --auth-backend even when the operator omitted it.
    pool_dispatch_template: PoolDispatchConfig | None = None
    auth_flags_resolved = AuthPoolFlags()
    if auth_account:
        if auth_include_labels and auth_exclude_labels:
            raise CLIError(
                "--auth-include-labels and --auth-exclude-labels are mutually exclusive."
            )
        # Default backend matches the dispatch backend so cloud workers
        # read from Secret Manager and local runs read from ~/.metaproc/.
        resolved_auth_backend = (
            auth_backend
            if auth_backend is not None
            else ("gcp-secret-manager" if backend == "gcp-worker" else "local")
        )
        if resolved_auth_backend == "gcp-secret-manager":
            project = MetaprocEnv.METAPROC_GCP_PROJECT.read_str(default="")
            if not project:
                raise CLIError(
                    "METAPROC_GCP_PROJECT is not set; required for --auth-backend "
                    "gcp-secret-manager. Export it or use --auth-backend local."
                )
            pool_user = MetaprocEnv.METAPROC_AUTH_POOL.read_str(
                default=""
            ) or MetaprocEnv.USER.read_str(default="unknown")
            auth_pool_backend_obj = gcp_backend(project, pool_user=pool_user)
        elif resolved_auth_backend == "local":
            auth_pool_backend_obj = local_backend()
        else:
            raise CLIError(
                f"--auth-backend must be 'local' or 'gcp-secret-manager', "
                f"got {resolved_auth_backend!r}."
            )
        # --auth-include-labels is the priority list. Selection itself
        # validates missing labels at startup (raises KeyError naming the
        # typo). --auth-exclude-labels are operator-level pin-outs for
        # the entire dispatch.
        #
        # Default policy switching (plan-2026-05-03 Goal #1):
        # ROUND_ROBIN is the dispatch default when --auth-include-labels
        # has ≥ 2 labels and --auth-policy is unset; PRIORITY_ORDER
        # otherwise. Explicit --auth-policy priority-order reproduces
        # legacy behavior bit-for-bit.
        if auth_policy:
            try:
                resolved_selection_policy = SelectionPolicy(auth_policy)
            except ValueError as exc:
                raise CLIError(
                    f"--auth-policy must be one of "
                    f"priority-order|round-robin|least-active, got {auth_policy!r}."
                ) from exc
        elif len(auth_include_labels) >= 2:
            resolved_selection_policy = SelectionPolicy.ROUND_ROBIN
        else:
            resolved_selection_policy = SelectionPolicy.PRIORITY_ORDER
        strategy = SelectionStrategy(
            policy=resolved_selection_policy,
            labels=tuple(auth_include_labels),
        )
        excluded_pairs = tuple((auth_account, lbl) for lbl in auth_exclude_labels)
        try:
            fallback_policy_enum = FallbackPolicy(auth_fallback_policy)
        except ValueError as exc:
            raise CLIError(
                f"--auth-fallback-policy must be one of "
                f"none|same-provider|cross-provider|both, got {auth_fallback_policy!r}."
            ) from exc
        coordinator = SlotCoordinator(auth_pool_backend_obj)
        # ``Path("")`` is the truthy ``PosixPath('.')``, so the bare
        # ``Path(...) or run_dir.parent`` short-circuit pre-2026-04-27 fell
        # through to ``Path('.')`` whenever ``RUNS_DIR`` was unset and wrote
        # slot dirs at CWD. Explicit empty-string check picks the right
        # fallback (the run's parent) in that case.
        runs_dir_for_pool = Path(variables["RUNS_DIR"])
        pool_dispatch_template = PoolDispatchConfig(
            coordinator=coordinator,
            adapter=auth_account,
            runs_dir=runs_dir_for_pool,
            run_id=run_context,
            step="",  # filled per-step in _execute_fan_out_step
            strategy=strategy,
            fallback_policy=fallback_policy_enum,
            exclude=excluded_pairs,
            cross_quota_group=auth_cross_quota_group,
        )
        # Worker-leg shape: the same five fields encoded for inner
        # `run-parallel --auth-*` reconstruction. ``resolved_auth_backend``
        # carries the worker-default plug-in so a cloud worker that the
        # operator did not explicitly point at gcp-secret-manager still
        # receives `--auth-backend gcp-secret-manager`.
        auth_flags_resolved = AuthPoolFlags(
            auth_account=auth_account,
            auth_backend=resolved_auth_backend,
            auth_fallback_policy=auth_fallback_policy,
            auth_policy=auth_policy or "",
            auth_include_labels=tuple(auth_include_labels),
            auth_exclude_labels=tuple(auth_exclude_labels),
            auth_cross_quota_group=auth_cross_quota_group,
        )
        priority_repr = list(strategy.labels) or "(any active)"
        out.progress(
            f"Auth pool: adapter={auth_account} backend={resolved_auth_backend} "
            f"strategy={resolved_selection_policy.value} labels={priority_repr} "
            f"fallback={fallback_policy_enum.value}"
        )

        # Pre-flight probe gate. Before any items dispatch, real-API
        # probe every label; mark expired/cooling labels in the pool
        # and remove them from the strategy. Eliminates the "all N
        # items hit a stale token at fan-out start" pathology — by
        # the time workers launch, every selectable label has a
        # freshly-validated credential.
        from metaproc.dispatch.pool_dispatch import (  # noqa: PLC0415 -- pre-existing local import; needs review
            pre_fan_out_probe,
        )

        def _on_probe(label: str, result: object) -> None:
            if result is None:
                out.progress(f"  pre-flight probe: {label} skipped (already non-active)")
            else:
                target = getattr(result, "target_status", "?")
                note = getattr(result, "note", "")
                out.progress(f"  pre-flight probe: {label} → {target} ({note})")

        out.progress("Pre-flight probe: validating each pool label before fan-out")
        # Per-label invocation sidecars land here so we can diff what the
        # probe actually invoked claude with against the laptop snapshot.
        # See plan-2026-05-01 § Phase 3.5.
        preflight_sidecar_dir = run_dir / ".state" / "preflight-probes"
        try:
            pool_dispatch_template = pre_fan_out_probe(
                pool_dispatch_template,
                auth_pool_backend_obj,
                on_probe=_on_probe,
                sidecar_dir=preflight_sidecar_dir,
            )
        except Exception as exc:  # noqa: BLE001
            out.error(f"pre-flight probe failed: {exc}")
            raise typer.Exit(code=1) from exc
        if pool_dispatch_template.strategy.labels != strategy.labels:
            out.progress(
                f"Pre-flight: filtered strategy from {list(strategy.labels)} "
                f"→ {list(pool_dispatch_template.strategy.labels)}"
            )

    # Ancestor verification for --from/--only (after auth-pool validation
    # so operator typos in --auth-account / --auth-include-labels surface
    # before "ancestor not completed" noise).
    if (from_step or only_step) and not force:
        errors = _verify_ancestors(run_dir, plan, active_step_ids, spec, variables)
        if errors:
            raise CLIError(
                f"Unsatisfied ancestors for --{'from' if from_step else 'only'} "
                f"{from_step or only_step}:\n"
                + "\n".join(errors)
                + "\nRun without --from/--only to execute the full process, or use --force."
            )

    overall_start = time.monotonic()

    # Orchestrate — build a plan with only active steps
    active_plan = Plan(
        generated_at=plan.generated_at,
        process=plan.process,
        params=plan.params,
        steps=[s for s in plan.steps if s.step_id in active_step_ids],
    )

    logs_dir = run_dir / LOGS_DIR
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Acquire orchestrator lease (prevents concurrent orchestrators on the same run).
    command_summary = f"run-process {spec.name} --backend {backend}"
    if variant:
        command_summary += f" --variant {variant}"
    if artifact_namespace:
        command_summary += f" --artifact-namespace {artifact_namespace}"
    if initial_concurrency is not None:
        command_summary += f" --initial-concurrency {initial_concurrency}"
    acquire_lease(run_dir, owner_type="local", command_summary=command_summary, force=force)

    install_subprocess_reaper_signal_handlers()

    finalization_state = FinalizationState.COMPLETED
    terminal_error: BaseException | None = None
    try:
        with (
            LeaseHeartbeat(run_dir),
            ProcessEventLogger(paths_mod.process_events_log(run_dir)) as events,
        ):
            asyncio.run(
                _orchestrate(
                    spec=spec,
                    plan=active_plan,
                    variables=variables,
                    process_path=process_path,
                    process_dir=process_dir,
                    run_dir=run_dir,
                    run_id=run_id,
                    backend_name=backend,
                    max_concurrency=max_concurrency,
                    initial_concurrency=initial_concurrency,
                    num_workers=num_workers,
                    machine_type=machine_type,
                    spot=spot,
                    variant_override=variant,
                    profile_files=profile_files,
                    skip_steps=skip_set,
                    force=force,
                    continue_on_error=continue_on_error,
                    continue_on_step_failure=continue_on_step_failure,
                    out=out,
                    events=events,
                    pool_dispatch_template=pool_dispatch_template,
                    auth_flags=auth_flags_resolved,
                    preflight_quota_guard=auth_preflight_quota_guard,
                )
            )

        output_errors = validate_process_outputs(spec, variables, process_dir, plan=plan)
        if output_errors:
            msg = "process output validation failed:\n  " + "\n  ".join(output_errors)
            raise CLIError(msg)
    except (TimeoutError, subprocess.TimeoutExpired) as exc:
        finalization_state = FinalizationState.TIMED_OUT
        terminal_error = exc
        raise
    except (KeyboardInterrupt, asyncio.CancelledError) as exc:
        finalization_state = FinalizationState.CANCELLED
        terminal_error = exc
        raise
    except BaseException as exc:
        finalization_state = FinalizationState.FAILED
        terminal_error = exc
        raise
    finally:
        try:
            try:
                finalize_run_resources(
                    run_dir,
                    outcome=finalization_state,
                    trigger="terminal",
                    terminal_error=terminal_error,
                    bundle=snapshot_bundle,
                )
            except Exception:  # noqa: BLE001 - reporting cannot replace process outcome
                log.exception("resource finalization failed for %s", run_dir)
            except BaseException:
                # Preserve an already-propagating process failure even if reporting is
                # interrupted. With no prior failure, retain normal BaseException
                # semantics after releasing the lease below.
                if terminal_error is None:
                    raise
                log.exception("resource finalization interrupted for %s", run_dir)
        finally:
            release_lease(run_dir)

    overall_elapsed = fmt_timedelta(time.monotonic() - overall_start)
    out.progress(f"\n=== Process '{spec.name}' completed ({overall_elapsed}) ===")
