"""Canonical path constants for metaproc runtime directories and files.

All directory names and filenames used by metaproc are defined here.
Import from this module instead of hardcoding path strings.
"""

from __future__ import annotations

import re
from pathlib import Path

from ruamel.yaml import YAMLError

from metaproc.errors import CLIError
from metaproc.io import artifact_exists, from_yaml_string, resolve_existing_artifact

# ── Item-key safety ────────────────────────────────────────────

ITEM_KEY_RE = re.compile(r"[A-Za-z0-9._\-]+")
"""Allowed characters for a resolved per-task item-key (used as a path component).

A ``for_each.key`` template (e.g. ``"{{item}}"``) is rendered with the
loop variables; the resulting string must fully match this pattern so it
is safe to use as a path component on every supported filesystem.
"""


def is_safe_item_key(key: str) -> bool:
    """Return whether *key* is safe to use as a filesystem path component."""
    return bool(ITEM_KEY_RE.fullmatch(key))


# ── Runtime directories (under each item or run dir) ────────────

STATE_DIR = ".state"
"""Engine state directory under a run dir (small, durable, needed for resume).

All engine state lives under ``<run_dir>/.state/`` with stable sub-namespaces:

- ``<run_dir>/.state/`` — run-level files (run-config.yaml, process-status.yaml,
  orchestrator-lease.yaml, overrides.yaml).
- ``<run_dir>/.state/steps/<step_id>/`` — per-step fan-out runner state
  (runpool-status.yaml, scale-state.yaml, scale-override.yaml,
  dispatch-manifest.yaml, claimed-items.yaml).
- ``<run_dir>/.state/workers/<worker_id>/`` — per-worker pool state
  (runpool-status.yaml).
- ``<run_dir>/.state/tasks/<step_id>/<item_key>/`` — per-task state
  (status.yaml, attempt.yaml, result.yaml, manual-ack.yaml).
"""

LOGS_DIR = ".logs"
"""Engine log directory under a run dir (large, ephemeral, safe to delete).

Log paths are organized by the thing that produced them:

- ``<run_dir>/.logs/`` — run-level logs (process-events.jsonl,
  dispatch-config-changes.jsonl).
- ``<run_dir>/.logs/runpool/steps/<step_id>/`` — per-step pool streams.
- ``<run_dir>/.logs/runpool/workers/<worker_id>/`` — per-worker pool streams.
- ``<run_dir>/.logs/tasks/<step_id>/`` — per-scalar-task captured process logs.
- ``<run_dir>/.logs/tasks/<step_id>/<item_key>/`` — per-fan-out-task logs.
- ``<run_dir>/.logs/derived/`` — derived indexes such as trace.jsonl.
- ``<run_dir>/.logs/tools/<tool_name>/`` — tool invocation streams.
"""

STEPS_SUBDIR = "steps"
"""Sub-namespace for per-step fan-out runner files."""

TASKS_SUBDIR = "tasks"
"""Sub-namespace inside ``.state/`` and ``.logs/`` for per-task files keyed by step+item."""

WORKERS_SUBDIR = "workers"
"""Sub-namespace for per-worker runpool files."""

RUNPOOL_SUBDIR = "runpool"
"""Sub-namespace inside ``.logs/`` for runpool-produced streams."""

DERIVED_SUBDIR = "derived"
"""Sub-namespace inside ``.logs/`` for derived, rebuildable streams."""

TOOLS_SUBDIR = "tools"
"""Sub-namespace inside ``.logs/`` for workflow tool invocation streams."""

RUN_LAYOUT_VERSION = "metaproc-run-layout/2"
"""Current metaproc run-directory layout marker written to run-config.yaml."""

# ── State files (inside STATE_DIR) ──────────────────────────────

STATUS_FILE = "status.yaml"
ATTEMPT_FILE = "attempt.yaml"
RESULT_FILE = "result.yaml"
MANUAL_ACK_FILE = "manual-ack.yaml"

# ── Process spec ────────────────────────────────────────────────

PROCESS_SPEC_SUFFIX = ".process.md"
"""Filename suffix for typed metaproc process specs (e.g. ``mine.process.md``).

A process-spec path must always be an existing file ending in this suffix.
"""

# ── Default run root ────────────────────────────────────────────

DEFAULT_RUN_ROOT = "runs/"

# ── Pool files (status in STATE_DIR, events in LOGS_DIR) ────────

POOL_STATUS_FILE = "runpool-status.yaml"
SCALE_STATE_FILE = "scale-state.yaml"
SCALE_OVERRIDE_FILE = "scale-override.yaml"
POOL_EVENTS_FILE = "runpool-events.jsonl"
"""Legacy runpool events filename used by pre-V2 run directories."""

RUNPOOL_EVENTS_FILE = "events.jsonl"
"""V2 runpool events filename under ``.logs/runpool/...`` scopes."""

RUNPOOL_HEALTH_FILE = "health.jsonl"
"""V2 runpool health-sample filename under ``.logs/runpool/...`` scopes."""

POOL_KILL_SENTINEL_FILE = "pool-kill-requested.yaml"

# ── Process-level files (in LOGS_DIR and STATE_DIR) ────────────

PROCESS_EVENTS_FILE = "process-events.jsonl"
"""Orchestrator DAG progress events (step lifecycle, worker dispatch)."""

DISPATCH_CONFIG_CHANGES_FILE = "dispatch-config-changes.jsonl"
"""Operator resume/config change audit stream."""

PROCESS_STATUS_FILE = "process-status.yaml"
"""Overall DAG execution state (derived from per-step status)."""

RUN_CONFIG_FILE = "run-config.yaml"
"""Immutable run identity written at creation, validated on resume."""

ORCHESTRATOR_LEASE_FILE = "orchestrator-lease.yaml"
"""Active orchestrator lease — heartbeat, owner identity, stale takeover."""

DISPATCH_MANIFEST_FILE = "dispatch-manifest.yaml"
"""Per-step dispatch manifest — worker job IDs for adoption on resume."""

CLAIMED_ITEMS_FILE = "claimed-items.yaml"
"""Per-worker claimed item registry for hot scale-up reconciliation."""

OVERRIDES_FILE = "overrides.yaml"
"""Run-scoped operator overrides for dependency satisfaction."""

TRACE_FILE = "trace.jsonl"
"""Derived trace stream filename."""

RESOURCE_EVENTS_FILE = "resource-events.jsonl"
"""Per-tool ResourceEvent JSONL stream filename.

Tools (today: external tool) emit one typed `ResourceEvent` per line
matching `metaproc.models.resources.ResourceEvent`; readers parse via
`ResourceEvent.model_validate_json(line)`. Replaces the pre-cutover
`invocations.jsonl` (no legacy fallback retained — see
`feedback_no_legacy_shims_by_default`)."""


# ── Run-dir helper paths ─────────────────────────────────────────


def run_state_dir(run_dir: Path) -> Path:
    """Return ``<run_dir>/.state/`` (run-level engine state branch)."""
    return run_dir / STATE_DIR


def run_logs_dir(run_dir: Path) -> Path:
    """Return ``<run_dir>/.logs/`` (run-level engine logs branch)."""
    return run_dir / LOGS_DIR


def run_config_file(run_dir: Path) -> Path:
    """Return run config path: ``<run_dir>/.state/run-config.yaml``."""
    return run_state_dir(run_dir) / RUN_CONFIG_FILE


def process_events_log(run_dir: Path) -> Path:
    """Return run-level process events path: ``<run_dir>/.logs/process-events.jsonl``."""
    return run_logs_dir(run_dir) / PROCESS_EVENTS_FILE


def dispatch_config_changes_log(run_dir: Path) -> Path:
    """Return V2 config-change stream: ``<run_dir>/.logs/dispatch-config-changes.jsonl``."""
    return run_logs_dir(run_dir) / DISPATCH_CONFIG_CHANGES_FILE


def legacy_dispatch_config_changes_log(run_dir: Path) -> Path:
    """Return legacy config-change stream: ``<run_dir>/.state/dispatch-config-changes.jsonl``."""
    return run_state_dir(run_dir) / DISPATCH_CONFIG_CHANGES_FILE


def runpool_logs_dir(run_dir: Path) -> Path:
    """Return runpool logs root: ``<run_dir>/.logs/runpool/``."""
    return run_logs_dir(run_dir) / RUNPOOL_SUBDIR


def step_state_dir(run_dir: Path, step_id: str) -> Path:
    """Return per-step runner state dir: ``<run_dir>/.state/steps/<step_id>/``."""
    return run_dir / STATE_DIR / STEPS_SUBDIR / step_id


def runpool_step_logs_dir(run_dir: Path, step_id: str) -> Path:
    """Return per-step runpool logs dir: ``<run_dir>/.logs/runpool/steps/<step_id>/``."""
    return runpool_logs_dir(run_dir) / STEPS_SUBDIR / step_id


def step_logs_dir(run_dir: Path, step_id: str) -> Path:
    """Return per-step runpool logs dir: ``<run_dir>/.logs/runpool/steps/<step_id>/``."""
    return runpool_step_logs_dir(run_dir, step_id)


def worker_dir_name(worker_id: str | int) -> str:
    """Return normalized worker directory name (``worker-<id>``)."""
    value = str(worker_id)
    if value.startswith("worker-"):
        return value
    return f"worker-{value}"


def worker_state_dir(run_dir: Path, worker_id: str | int) -> Path:
    """Return worker state dir: ``<run_dir>/.state/workers/<worker_id>/``."""
    return run_state_dir(run_dir) / WORKERS_SUBDIR / worker_dir_name(worker_id)


def legacy_worker_state_dir(run_dir: Path, worker_id: str | int) -> Path:
    """Return legacy worker state dir: ``<run_dir>/.state/worker-<id>/``."""
    return run_state_dir(run_dir) / worker_dir_name(worker_id)


def worker_logs_dir(run_dir: Path, worker_id: str | int) -> Path:
    """Return worker runpool logs dir: ``<run_dir>/.logs/runpool/workers/<worker_id>/``."""
    return runpool_logs_dir(run_dir) / WORKERS_SUBDIR / worker_dir_name(worker_id)


def legacy_worker_logs_dir(run_dir: Path, worker_id: str | int) -> Path:
    """Return legacy worker logs dir: ``<run_dir>/.logs/worker-<id>/``."""
    return run_logs_dir(run_dir) / worker_dir_name(worker_id)


def worker_status_file(run_dir: Path, worker_id: str | int) -> Path:
    """Return worker runpool status path in the V2 layout."""
    return worker_state_dir(run_dir, worker_id) / POOL_STATUS_FILE


def legacy_worker_status_file(run_dir: Path, worker_id: str | int) -> Path:
    """Return worker runpool status path in the legacy layout."""
    return legacy_worker_state_dir(run_dir, worker_id) / POOL_STATUS_FILE


def runpool_step_events(run_dir: Path, step_id: str) -> Path:
    """Return V2 per-step runpool events stream path."""
    return runpool_step_logs_dir(run_dir, step_id) / RUNPOOL_EVENTS_FILE


def runpool_step_health(run_dir: Path, step_id: str) -> Path:
    """Return V2 per-step runpool health stream path."""
    return runpool_step_logs_dir(run_dir, step_id) / RUNPOOL_HEALTH_FILE


def legacy_runpool_step_events(run_dir: Path, step_id: str) -> Path:
    """Return legacy per-step runpool events stream path."""
    return run_logs_dir(run_dir) / STEPS_SUBDIR / step_id / POOL_EVENTS_FILE


def runpool_worker_events(run_dir: Path, worker_id: str | int) -> Path:
    """Return V2 per-worker runpool events stream path."""
    return worker_logs_dir(run_dir, worker_id) / RUNPOOL_EVENTS_FILE


def runpool_worker_health(run_dir: Path, worker_id: str | int) -> Path:
    """Return V2 per-worker runpool health stream path."""
    return worker_logs_dir(run_dir, worker_id) / RUNPOOL_HEALTH_FILE


def legacy_runpool_worker_events(run_dir: Path, worker_id: str | int) -> Path:
    """Return legacy per-worker runpool events stream path."""
    return legacy_worker_logs_dir(run_dir, worker_id) / POOL_EVENTS_FILE


def runpool_events(run_dir: Path) -> Path:
    """Return V2 unscoped runpool events stream path."""
    return runpool_logs_dir(run_dir) / RUNPOOL_EVENTS_FILE


def runpool_health(run_dir: Path) -> Path:
    """Return V2 unscoped runpool health stream path."""
    return runpool_logs_dir(run_dir) / RUNPOOL_HEALTH_FILE


def legacy_runpool_events(run_dir: Path) -> Path:
    """Return legacy unscoped runpool events stream path."""
    return run_logs_dir(run_dir) / POOL_EVENTS_FILE


def task_logs_parent_dir(run_dir: Path, step_id: str) -> Path:
    """Return per-step task logs parent: ``<run_dir>/.logs/tasks/<step_id>/``."""
    return run_logs_dir(run_dir) / TASKS_SUBDIR / step_id


def task_state_dir(run_dir: Path, step_id: str, item_key: str) -> Path:
    """Return per-task state dir: ``<run_dir>/.state/tasks/<step_id>/<item_key>/``."""
    return run_dir / STATE_DIR / TASKS_SUBDIR / step_id / item_key


def task_logs_dir(run_dir: Path, step_id: str, item_key: str) -> Path:
    """Return per-task logs dir: ``<run_dir>/.logs/tasks/<step_id>/<item_key>/``."""
    return task_logs_parent_dir(run_dir, step_id) / item_key


def derived_logs_dir(run_dir: Path) -> Path:
    """Return derived logs root: ``<run_dir>/.logs/derived/``."""
    return run_logs_dir(run_dir) / DERIVED_SUBDIR


def trace_out(run_dir: Path) -> Path:
    """Return V2 trace output path: ``<run_dir>/.logs/derived/trace.jsonl``."""
    return derived_logs_dir(run_dir) / TRACE_FILE


def legacy_trace_out(run_dir: Path) -> Path:
    """Return legacy trace output path: ``<run_dir>/.logs/trace.jsonl``."""
    return run_logs_dir(run_dir) / TRACE_FILE


def tool_log_dir(run_dir: Path, tool_name: str) -> Path:
    """Return V2 tool log dir: ``<run_dir>/.logs/tools/<tool_name>/``."""
    return run_logs_dir(run_dir) / TOOLS_SUBDIR / tool_name


def tool_resource_events_log(run_dir: Path, tool_name: str) -> Path:
    """Return the ResourceEvent JSONL stream path for ``tool_name``.

    Layout: ``<run_dir>/.logs/tools/<tool_name>/resource-events.jsonl``.
    Tools (today: external tool) write typed `ResourceEvent` lines here; readers
    parse with `ResourceEvent.model_validate_json`.
    """
    return tool_log_dir(run_dir, tool_name) / RESOURCE_EVENTS_FILE


def read_metaproc_layout(run_dir: Path) -> str | None:
    """Read ``metaproc_layout`` from run-config.yaml, returning ``None`` if absent."""
    config_path = run_config_file(run_dir)
    try:
        text = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise CLIError(
            f"Failed to read metaproc layout metadata from {config_path}: {exc}"
        ) from exc

    try:
        data = from_yaml_string(text)
    except YAMLError as exc:
        raise CLIError(
            f"Failed to parse metaproc layout metadata from {config_path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise CLIError(f"Invalid metaproc layout metadata in {config_path}: expected a mapping")
    layout = data.get("metaproc_layout")
    if isinstance(layout, str):
        return layout
    return None


def is_v2_run_layout(run_dir: Path) -> bool:
    """Return whether run-config.yaml declares the V2 metaproc layout."""
    return read_metaproc_layout(run_dir) == RUN_LAYOUT_VERSION


def resolve_v2_or_legacy_stream(run_dir: Path, v2_path: Path, legacy_path: Path) -> Path:
    """Resolve a stream path using exact per-stream V2-to-legacy fallback.

    Marked V2 runs always read the V2 path. Unmarked runs prefer a present V2
    stream, then fall back to that stream's exact legacy path. Stream reads are
    compression-aware: ``foo.jsonl.gz`` satisfies a logical ``foo.jsonl`` path.
    """
    if is_v2_run_layout(run_dir):
        return resolve_existing_artifact(v2_path)
    if artifact_exists(v2_path):
        return resolve_existing_artifact(v2_path)
    if artifact_exists(legacy_path):
        return resolve_existing_artifact(legacy_path)
    return legacy_path


def dispatch_config_changes_for_read(run_dir: Path) -> Path:
    """Return config-change stream path using exact V2-to-legacy fallback."""
    return resolve_v2_or_legacy_stream(
        run_dir,
        dispatch_config_changes_log(run_dir),
        legacy_dispatch_config_changes_log(run_dir),
    )


def runpool_step_events_for_read(run_dir: Path, step_id: str) -> Path:
    """Return per-step runpool events path using exact V2-to-legacy fallback."""
    return resolve_v2_or_legacy_stream(
        run_dir,
        runpool_step_events(run_dir, step_id),
        legacy_runpool_step_events(run_dir, step_id),
    )


def runpool_worker_events_for_read(run_dir: Path, worker_id: str | int) -> Path:
    """Return per-worker runpool events path using exact V2-to-legacy fallback."""
    return resolve_v2_or_legacy_stream(
        run_dir,
        runpool_worker_events(run_dir, worker_id),
        legacy_runpool_worker_events(run_dir, worker_id),
    )


def runpool_events_for_read(run_dir: Path) -> Path:
    """Return unscoped runpool events path using exact V2-to-legacy fallback."""
    return resolve_v2_or_legacy_stream(
        run_dir, runpool_events(run_dir), legacy_runpool_events(run_dir)
    )


def worker_status_file_for_read(run_dir: Path, worker_id: str | int) -> Path:
    """Return per-worker status path using exact V2-to-legacy fallback."""
    return resolve_v2_or_legacy_stream(
        run_dir,
        worker_status_file(run_dir, worker_id),
        legacy_worker_status_file(run_dir, worker_id),
    )


def trace_out_for_read(run_dir: Path) -> Path:
    """Return trace output path using exact V2-to-legacy fallback."""
    return resolve_v2_or_legacy_stream(run_dir, trace_out(run_dir), legacy_trace_out(run_dir))


# ── Path containment utility ──────────────────────────────────


def is_path_under(root: Path, candidate: Path) -> bool:
    """Return whether *candidate* is equal to or nested under *root*."""
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False
