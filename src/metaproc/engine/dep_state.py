"""Dep-graph runtime state helpers for ``metaproc deps``."""

from __future__ import annotations

import glob as glob_mod
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError
from ruamel.yaml import YAMLError as _RuamelYAMLError

from metaproc.engine.placeholders import TEMPLATE_PATTERN
from metaproc.io import read_yaml_file
from metaproc.io.state_io import read_attempt_at, read_result_at, read_status_at
from metaproc.models.plan import Plan, ResolvedDep, ResolvedStep
from metaproc.models.runtime import StepState
from metaproc.paths import STATE_DIR, STATUS_FILE

_PROCESS_STATUS_FILE = "process-status.yaml"
_STALE_SUFFIX = ".yaml.stale"

log = logging.getLogger(__name__)

# Broader exception tuple for read_attempt / read_result / read_item_status:
# a malformed or legacy-schema state file on disk must never crash the dep
# scan — these files come from arbitrary prior runs (different schema
# versions, or narrative strings with unquoted colons), so we log and skip
# rather than propagate the error up through the CLI. All YAML reads go
# through frontmatter_format (ruamel.yaml under the hood).
_STATE_READ_ERRORS = (
    PydanticValidationError,
    _RuamelYAMLError,
    ValueError,
)


@dataclass
class StepRuntimeSummary:
    """Observed runtime records for one producer step under a run directory."""

    started: bool = False
    running: bool = False
    attempt_step_hash: str | None = None
    result_step_hash: str | None = None


def fingerprint_step(step: ResolvedStep, *, require_referenced_files: bool = True) -> str:
    """Return a stable fingerprint covering both the step contract and the
    bytes of any runbook/prompt file the step references.

    Editing a step's prompt content (the markdown a step's agent loads at
    runtime) flips the fingerprint, so a rerun against the same RUN_ID
    re-executes that step (and, via the orchestrator's cascade, anything
    downstream). The contract part — id, declared I/O, adapter, etc. —
    still participates via the resolved-model serialization.

    Referenced files: every entry in ``prompt_paths`` plus ``uses_path``
    (composite). Entries that still contain template placeholders
    (``{{ }}``) are skipped: they cannot be opened, and the literal
    template string is already in the model payload.

    An absolute path that does not exist on disk is a real misconfiguration
    and raises ``FileNotFoundError`` loudly, unless the caller passes
    ``require_referenced_files=False``. A step may reference a file an earlier
    step in the same process produces, so before the run starts that file is
    legitimately absent; the plan projection fingerprints what exists and the
    execution-time callers keep the strict check, where a missing file really
    is a misconfiguration. A relative path that cannot be
    resolved from the caller's CWD logs a warning and is skipped from the
    content hash — ``fingerprint_step`` runs in many contexts (dry-run,
    deps inspection, runtime) and not all of them know the process_dir
    needed to canonicalize a relative path. The contract part of the
    fingerprint still participates so the step id, I/O, and adapter
    changes are still detected; only the content-edit sensitivity is lost
    for that one entry.

    Runtime-discovered fan-out items and their filtered count are excluded.
    The same authored step may be fingerprinted before and after a generated
    roster exists, and discovery results are execution state rather than part
    of the step definition.
    """
    payload = step.model_dump(
        mode="json",
        exclude_none=True,
        exclude={"fan_out": {"items", "filtered_count"}},
    )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    hasher = hashlib.sha256(encoded)
    for path_str in _referenced_runbook_paths(step):
        path = Path(path_str)
        try:
            content = path.read_bytes()
        except FileNotFoundError as err:
            if path.is_absolute() and require_referenced_files:
                raise FileNotFoundError(
                    f"step '{step.step_id}': referenced runbook {path_str!r} does not "
                    f"exist — cannot compute step fingerprint"
                ) from err
            if path.is_absolute():
                continue
            log.warning(
                "step %r: relative runbook path %r not readable from cwd %s; "
                "skipping its content from the fingerprint (contract part still hashed)",
                step.step_id,
                path_str,
                Path.cwd(),
            )
            continue
        hasher.update(b"\x00")
        hasher.update(content)
    return hasher.hexdigest()[:16]


def _referenced_runbook_paths(step: ResolvedStep) -> list[str]:
    """File-path fields on a ``ResolvedStep`` whose bytes participate in the
    fingerprint. Unresolved-template entries (containing ``{{ }}``) are
    filtered out — they cannot be opened, and their literal form is already
    captured by the model payload.
    """
    candidates: list[str] = list(step.prompt_paths or [])
    if step.uses_path:
        candidates.append(step.uses_path)
    return [p for p in candidates if p and "{{" not in p and "}}" not in p]


def infer_dep_states(plan: Plan, run_dir: Path | None = None) -> dict[str, str]:
    """Infer runtime state for every resolved dep in *plan*."""
    step_hashes = {step.step_id: fingerprint_step(step) for step in plan.steps}
    runtime_index = build_step_runtime_index(run_dir) if run_dir and run_dir.exists() else {}

    states: dict[str, str] = {}
    for dep_name, dep in plan.deps.items():
        producer_step_id = dep.produced_by.split(".", 1)[0] if dep.produced_by else None
        runtime = runtime_index.get(producer_step_id or "")
        matches = resolve_dep_matches(dep.path)
        exists = bool(matches)
        current_hash = step_hashes.get(producer_step_id or "")
        recorded_hash = None
        if runtime is not None:
            recorded_hash = runtime.result_step_hash or runtime.attempt_step_hash

        if runtime is not None and runtime.running:
            states[dep_name] = "in-flight"
            continue
        if exists and current_hash and recorded_hash and current_hash != recorded_hash:
            states[dep_name] = "stale"
            continue
        if exists:
            states[dep_name] = "present"
            continue
        if producer_step_id is None:
            states[dep_name] = "missing"
            continue
        if runtime is not None and runtime.started:
            states[dep_name] = "missing"
            continue
        states[dep_name] = "not-yet-due"
    return states


def build_dep_report(plan: Plan, run_dir: Path | None = None) -> dict[str, ResolvedDep]:
    """Return resolved deps annotated with inferred runtime state."""
    states = infer_dep_states(plan, run_dir)
    return {
        dep_name: dep.model_copy(update={"state": states.get(dep_name)})
        for dep_name, dep in plan.deps.items()
    }


def resolve_dep_matches(path_template: str) -> list[Path]:
    """Resolve a dep path, falling back to globbing unresolved placeholders."""
    if "{{" not in path_template:
        path = Path(path_template)
        return [path] if path.exists() else []
    pattern = TEMPLATE_PATTERN.sub("*", path_template)
    return [Path(match) for match in glob_mod.glob(pattern)]


def build_step_runtime_index(run_dir: Path) -> dict[str, StepRuntimeSummary]:
    """Scan a run directory for per-task status/attempt/result records.

    Walks ``<run>/.state/tasks/<step_id>/`` (non-fan-out steps) and
    ``<run>/.state/tasks/<step_id>/<item>/`` (fan-out items). Each task's
    state directory holds ``status.yaml`` / ``attempt.yaml`` / ``result.yaml``
    directly — there is no inner ``.state/`` subdir.
    """
    summaries: dict[str, StepRuntimeSummary] = {}
    tasks_root = run_dir / STATE_DIR / "tasks"
    if not tasks_root.exists():
        return summaries

    # Each subdir of tasks_root is a step_id; status.yaml may live directly
    # under <step_id>/ (non-fan-out) or under <step_id>/<item_key>/ (fan-out).
    for status_path in tasks_root.rglob(STATUS_FILE):
        state_dir = status_path.parent
        try:
            status = read_status_at(state_dir)
        except _STATE_READ_ERRORS as exc:
            log.debug("skipping malformed status.yaml in %s: %s", state_dir, exc)
            continue
        if status is None:
            continue
        summary = summaries.setdefault(status.step_id, StepRuntimeSummary())
        summary.started = True
        summary.running = summary.running or status.state == "running"

        # Defensive: a legacy-schema / malformed attempt.yaml or result.yaml
        # must not crash the whole scan. Skip the unreadable record and carry
        # on — the caller still gets status/fingerprint data from readable
        # siblings.
        try:
            attempt = read_attempt_at(state_dir)
        except _STATE_READ_ERRORS as exc:
            log.debug("skipping malformed attempt.yaml in %s: %s", state_dir, exc)
            attempt = None
        if attempt is not None and attempt.step_hash:
            summary.attempt_step_hash = attempt.step_hash

        try:
            result = read_result_at(state_dir)
        except _STATE_READ_ERRORS as exc:
            log.debug("skipping malformed result.yaml in %s: %s", state_dir, exc)
            result = None
        if result is not None and result.step_hash:
            summary.result_step_hash = result.step_hash

    return summaries


# ── Step-state computation ────────────────────────────────────────


def _read_process_status_raw(run_dir: Path) -> dict[str, object] | None:
    """Read ``.state/process-status.yaml`` as a raw dict, or ``None``.

    Shared by ``recorded_step_hash`` and ``compute_step_state``. A
    duplicate lives in ``commands/run_process.py`` for orchestrator code
    paths to avoid an import cycle through ``run_process``.
    """
    path = run_dir / STATE_DIR / _PROCESS_STATUS_FILE
    if not path.exists():
        return None
    try:
        data = read_yaml_file(path)
    except _RuamelYAMLError:
        return None
    return data if isinstance(data, dict) else None


def recorded_step_hash(run_dir: Path, step_id: str) -> str | None:
    """Return the step fingerprint recorded at the prior completion, or None.

    Reads from the step-level mirror in ``process-status.yaml`` first.
    Falls back to per-task ``result.yaml`` / ``attempt.yaml`` step_hash
    for completion records that lack the mirror entry. Returns ``None``
    when no source has a fingerprint on file — callers treat that as a
    legacy completion.
    """
    data = _read_process_status_raw(run_dir)
    if isinstance(data, dict):
        steps = data.get("steps")
        if isinstance(steps, dict):
            entry = steps.get(step_id)
            if isinstance(entry, dict):
                h = entry.get("recorded_step_hash")
                if isinstance(h, str) and h:
                    return h

    summary = build_step_runtime_index(run_dir).get(step_id)
    if summary is not None:
        return summary.result_step_hash or summary.attempt_step_hash
    return None


def _process_status_step_state(run_dir: Path, step_id: str) -> str | None:
    data = _read_process_status_raw(run_dir)
    if not isinstance(data, dict):
        return None
    steps = data.get("steps")
    if not isinstance(steps, dict):
        return None
    entry = steps.get(step_id)
    if not isinstance(entry, dict):
        return None
    state = entry.get("state")
    return state if isinstance(state, str) else None


def _scan_step_task_files(run_dir: Path, step_id: str) -> tuple[bool, bool, bool]:
    """Walk ``<run>/.state/tasks/<step_id>/`` and report observed signals.

    Returns ``(is_running, has_stale, has_completed_status_yaml)``:

    - ``is_running``: at least one ``status.yaml`` reports state=running.
    - ``has_stale``: at least one ``status.yaml.stale`` exists (renamed by
      ``--force`` or by the fingerprint cascade).
    - ``has_completed_status_yaml``: at least one ``status.yaml`` reports
      state=completed. Used as a fallback when ``process-status.yaml`` is
      absent (single-step runs / legacy layouts).
    """
    is_running = False
    has_stale = False
    has_completed = False

    tasks_root = run_dir / STATE_DIR / "tasks" / step_id
    if not tasks_root.exists():
        return is_running, has_stale, has_completed

    for status_path in tasks_root.rglob(STATUS_FILE):
        try:
            rec = read_status_at(status_path.parent)
        except _STATE_READ_ERRORS as exc:
            log.debug("skipping malformed status.yaml in %s: %s", status_path.parent, exc)
            continue
        if rec is None:
            continue
        if rec.state == "running":
            is_running = True
        elif rec.state == "completed":
            has_completed = True

    for _ in tasks_root.rglob("*" + _STALE_SUFFIX):
        has_stale = True
        break

    return is_running, has_stale, has_completed


def compute_step_state(run_dir: Path, plan: Plan, step_id: str) -> StepState:
    """Return the single per-step ``StepState`` for *step_id* under *run_dir*.

    Single source of truth for "what state is this step in" — the text
    formatter, JSON formatter, and any future remote-status RPC all call
    here so the rules live in exactly one place. Decision order (first
    match wins):

    1. ``in_flight``: any per-task ``status.yaml`` reports ``running``, or
       the process-status mirror says ``running``.
    2. ``invalidated``: any ``status.yaml.stale`` exists under the step's
       task state dir (renamed by ``--force`` or the fingerprint cascade).
    3. ``stale``: the step is otherwise considered completed AND a
       ``recorded_step_hash`` exists that differs from
       ``fingerprint_step(step)``.
    4. ``current``: the step is considered completed and either matches
       the current fingerprint or has no recorded fingerprint at all
       (legacy completion).
    5. ``missing``: anything else — never started, or started-and-failed
       without a completion record.
    """
    is_running, has_stale, has_completed_status = _scan_step_task_files(run_dir, step_id)
    ps_state = _process_status_step_state(run_dir, step_id)

    if is_running or ps_state == "running":
        return StepState.in_flight
    if has_stale:
        return StepState.invalidated

    is_completed = has_completed_status or ps_state == "completed"
    if not is_completed:
        return StepState.missing

    step = next((s for s in plan.steps if s.step_id == step_id), None)
    if step is None:
        # Plan no longer declares this step (e.g. renamed) — treat the
        # completion as legacy rather than inventing a fingerprint.
        return StepState.current

    recorded = recorded_step_hash(run_dir, step_id)
    if recorded is None:
        return StepState.current
    if recorded == fingerprint_step(step):
        return StepState.current
    return StepState.stale
