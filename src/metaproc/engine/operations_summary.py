"""Fold a run's on-disk evidence into the operations summary.

The fold reads only evidence already on disk: process status and run plans in every
scope, task and attempt records, transcript terminal results, RunPool event and health
streams, and the resource usage summary. It never calls the resource recovery path and
never writes a ``.jsonl``, so writing its output cannot mark resource projections stale.

Memory stays bounded by the largest small file it parses: transcripts are read from
their tail, and only transcripts large enough to hold a capped tool result are scanned
end to end, in fixed-size chunks that never retain a whole line.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml
from frontmatter_format import fmf_split_frontmatter, fmf_write
from strif import atomic_output_file, atomic_write_text

from metaproc import paths as paths_mod
from metaproc.engine.operations_render import render_summary_markdown
from metaproc.io import ArtifactPath, iter_jsonl_objects
from metaproc.io.gz_io import artifact_sidecar_path
from metaproc.models.operations_summary import (
    OPERATIONS_SUMMARY_CONTRACT,
    OPERATIONS_SUMMARY_ENVELOPE,
    AgentFigures,
    AgentOperationsSummary,
    Distribution,
    ItemChain,
    ItemFigures,
    ItemStage,
    MeterFigure,
    ParallelismFigures,
    PoolRow,
    ResourceFigures,
    RetryFigures,
    RetryStepRow,
    RunFigures,
    SetupFigures,
    StageItemStats,
    StageRow,
    StageStepStats,
    StepFigures,
    StepRef,
    StepTypeRow,
    TokenFigures,
)
from metaproc.models.resource_budget import FinalizationState
from metaproc.models.resources import UnpricedModel
from metaproc.runpool.log_files import scope_runpool_event_files, scope_runpool_health_files

log = logging.getLogger(__name__)

OPERATIONS_SUMMARY_FILE = "operations-summary.md"
OPERATIONS_SUMMARY_SCHEMA_RELATIVE = ".state/schemas/agent-operations-summary.v1.schema.yaml"
RESOURCE_USAGE_SUMMARY_FILE = "resource-usage-summary.md"

TOOL_RESULT_CAP_BYTES = 16 * 1024 * 1024
"""A transcript line at least this long is treated as carrying a capped tool result.

Gemini CLI caps a tool result at 16,777,287 characters; a JSONL line carrying one encodes
to at least 16 MiB. The figure counts encoded bytes, not characters.
"""

TRANSCRIPT_TAIL_BYTES = 256 * 1024
SCAN_CHUNK_BYTES = 1024 * 1024
RESULT_LINE_MAX_BYTES = 4 * 1024 * 1024
LINE_PREFIX_BYTES = 512
RUN_SIZE_WALK_LIMIT = 1_000_000
SLOWEST_ITEM_COUNT = 5

_TASKS = paths_mod.TASKS_SUBDIR
_NEVER = datetime.min.replace(tzinfo=UTC)
_ATTEMPT_RECORD_SCHEMA_PREFIX = "metaproc:TaskAttemptRecord/"
_REVISION_KEY = re.compile(r"^[A-Za-z0-9_]*_revision$", re.IGNORECASE)
_MODEL_FLAGS = ("-m", "--model")
_RESULT_MARKERS = (b'"type":"result"', b'"type": "result"')
_RESULT_MARKER_WINDOW = 128
_TOOL_RESULT_MARKERS = (b"tool_result", b"tool_use_result", b"toolUseResult", b"function_response")

_YAML_LOADER: type[yaml.SafeLoader] = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


# ── Public surface ────────────────────────────────────────────────


def build_operations_summary(
    run_dir: Path,
    *,
    outcome: FinalizationState | None = None,
    trigger: Literal["finalization", "status", "command"] = "command",
    generated_at: datetime | None = None,
) -> AgentOperationsSummary:
    """Read a run tree and return its operations summary without writing anything.

    *outcome* is the causal terminal state when the caller knows it, as run finalization
    does; otherwise run state comes from the resource usage summary, then process status.
    Every section is built independently, so a failure reading one section's evidence is
    recorded as that section's unavailable reason and never propagates.
    """
    started = time.monotonic()
    evidence = _Evidence.load(run_dir)
    unavailable: dict[str, str] = {}

    run = _contained("run", unavailable, lambda: _run_figures(evidence, outcome))
    elapsed_s = run.elapsed_s if run is not None else None
    stages = _contained("stages", unavailable, lambda: _stage_rows(evidence, elapsed_s))
    setup = _contained("setup", unavailable, lambda: _setup_figures(evidence, stages, elapsed_s))
    items = _contained("items", unavailable, lambda: _item_figures(evidence))
    steps = _contained("steps", unavailable, lambda: _step_figures(evidence))
    health_errors: dict[str, str] = {}
    health = _contained("health", health_errors, lambda: _read_health_samples(evidence))
    parallelism = _contained(
        "parallelism", unavailable, lambda: _parallelism_figures(evidence, health)
    )
    retries = _contained("retries", unavailable, lambda: _retry_figures(evidence))
    agents = _contained("agents", unavailable, lambda: _agent_figures(evidence))
    resource_figures = _contained(
        "resources",
        unavailable,
        lambda: _resource_figures(evidence, health, health_errors.get("health")),
    )

    return AgentOperationsSummary(
        run_id=evidence.run_id,
        generated_at=generated_at or datetime.now(UTC),
        trigger=trigger,
        extraction_s=round(time.monotonic() - started, 3),
        run=run,
        setup=setup,
        stages=stages,
        items=items,
        steps=steps,
        parallelism=parallelism,
        retries=retries,
        agents=agents,
        resources=resource_figures,
        unavailable=unavailable,
    )


def write_operations_summary(summary: AgentOperationsSummary, run_dir: Path) -> Path:
    """Persist the summary as frontmatter Markdown with its compiled schema sidecar."""
    schema_path = run_dir / OPERATIONS_SUMMARY_SCHEMA_RELATIVE
    _write_schema_sidecar(schema_path)
    target = run_dir / OPERATIONS_SUMMARY_FILE
    with atomic_output_file(target) as tmp_path:
        fmf_write(Path(tmp_path), render_summary_markdown(summary), summary_metadata(summary))
    return target


def summary_metadata(summary: AgentOperationsSummary) -> dict[str, Any]:
    """Return the frontmatter mapping written for *summary*."""
    return {
        "softschema": {
            "contract": OPERATIONS_SUMMARY_CONTRACT,
            "schema": OPERATIONS_SUMMARY_SCHEMA_RELATIVE,
            "envelope": OPERATIONS_SUMMARY_ENVELOPE,
            "status": "enforced",
        },
        OPERATIONS_SUMMARY_ENVELOPE: summary.model_dump(mode="json"),
    }


@dataclass(frozen=True)
class OperationsSummaryRead:
    """What reading one run's ``operations-summary.md`` found.

    A caller that cannot tell a run that wrote no summary from one whose summary no
    longer reads will silently recompute the second, and so will not learn that a
    published document stopped reading. The two are separate states here.
    """

    path: Path
    """Where the document would be, whether or not one is there."""
    summary: AgentOperationsSummary | None = None
    error: str | None = None
    """Why a document that is there did not read, or ``None`` when none is there."""

    @property
    def absent(self) -> bool:
        """No document is there to read."""
        return self.summary is None and self.error is None

    @property
    def unreadable(self) -> bool:
        """A document is there and did not read."""
        return self.error is not None


def read_operations_summary(run_dir: Path) -> OperationsSummaryRead:
    """Read the written summary for *run_dir*, separating absent from unreadable.

    Never raises, so a caller that falls back to folding the run tree keeps working. A
    document that does not parse, does not carry this contract, or does not validate is
    returned unreadable with the reason, and logged with its path, so the fallback can
    say it happened rather than reading as an ordinary absence.
    """
    path = run_dir / OPERATIONS_SUMMARY_FILE
    if not path.is_file():
        return OperationsSummaryRead(path)
    try:
        metadata = _read_frontmatter(path)
        if not isinstance(metadata, Mapping):
            return _unreadable(path, "the file carries no frontmatter mapping")
        envelope = metadata.get("softschema")
        contract = envelope.get("contract") if isinstance(envelope, Mapping) else None
        if contract != OPERATIONS_SUMMARY_CONTRACT:
            return _unreadable(path, f"declares contract {contract!r}, not the summary contract")
        summary = AgentOperationsSummary.model_validate(metadata.get(OPERATIONS_SUMMARY_ENVELOPE))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return _unreadable(path, f"{type(exc).__name__}: {exc}")
    return OperationsSummaryRead(path, summary=summary)


def _unreadable(path: Path, error: str) -> OperationsSummaryRead:
    log.warning("operations summary at %s did not read: %s", path, error)
    return OperationsSummaryRead(path, error=error)


def finalize_operations_summary(
    run_dir: Path,
    *,
    outcome: FinalizationState | None,
    trigger: Literal["finalization", "status"] = "finalization",
) -> Path | None:
    """Write the summary at run finalization; log and return ``None`` on any failure.

    An observability artifact must never change a run's outcome. ``metaproc status``
    passes ``trigger="status"`` and no outcome when it recovers a run that ended without
    finalizing, such as one killed by a signal, so run state comes from the resource usage
    summary that recovery just wrote.
    """
    try:
        summary = build_operations_summary(run_dir, outcome=outcome, trigger=trigger)
        return write_operations_summary(summary, run_dir)
    except Exception:  # noqa: BLE001 - reporting cannot replace process outcome
        log.exception("operations summary failed for %s", run_dir)
        return None


# ── Evidence loading ──────────────────────────────────────────────


@dataclass
class _Scope:
    """One runtime scope: the run root or a nested composite scope."""

    path: Path
    parts: tuple[str, ...]
    status: Mapping[str, Any] | None
    plan_steps: dict[str, Mapping[str, Any]]

    @property
    def process(self) -> str:
        name = self.status.get("process") if self.status else None
        return name if isinstance(name, str) and name else "/".join(self.parts) or "root"

    def step_entries(self) -> dict[str, Mapping[str, Any]]:
        steps = self.status.get("steps") if self.status else None
        if not isinstance(steps, Mapping):
            return {}
        return {str(k): v for k, v in steps.items() if isinstance(v, Mapping)}


@dataclass
class _Evidence:
    """Everything the fold reads, loaded once."""

    run_dir: Path
    run_id: str
    config: Mapping[str, Any]
    resource_summary: Mapping[str, Any] | None
    resource_summary_error: str | None
    scopes: list[_Scope]
    root_plan_error: str | None
    ignored_state_dirs: int = 0
    unreadable_plans: dict[str, str] = field(default_factory=dict)
    by_path: dict[Path, _Scope] = field(default_factory=dict)
    item_runs: dict[tuple[Path, str, str], _ItemRun] = field(default_factory=dict)

    @property
    def root(self) -> _Scope:
        return self.scopes[0]

    @classmethod
    def load(cls, run_dir: Path) -> _Evidence:
        scopes: list[_Scope] = []
        root_plan_error: str | None = None
        unreadable_plans: dict[str, str] = {}
        ignored = 0
        root_has_plan = (run_dir / paths_mod.STATE_DIR / paths_mod.RUN_PLAN_FILE).is_file()
        # A run that executed as a child scope of a larger run records every plan path from
        # that run's root, and the requested root's own plan names the prefix. It is empty
        # for a top-level run, and unknown when the root plan cannot be read.
        root_scope_path: list[str] | None = []
        for scope_dir in paths_mod.iter_composite_run_dirs(run_dir):
            state_dir = scope_dir / paths_mod.STATE_DIR
            plan_path = state_dir / paths_mod.RUN_PLAN_FILE
            parts = scope_dir.relative_to(run_dir).parts if scope_dir != run_dir else ()
            plan_steps: dict[str, Mapping[str, Any]] = {}
            scope_path: list[str] | None = None
            plan_error: str | None = None
            plan_unreadable = False
            try:
                plan_steps, scope_path = _plan_steps(_load_yaml_mapping(plan_path, strict=True))
            except FileNotFoundError as exc:
                plan_error = _describe_error(plan_path, exc, run_dir)
            except (OSError, ValueError, yaml.YAMLError) as exc:
                plan_error = _describe_error(plan_path, exc, run_dir)
                plan_unreadable = True
                unreadable_plans[str(plan_path.relative_to(run_dir))] = _error_detail(exc)
            if scope_dir == run_dir:
                root_plan_error = plan_error
                root_scope_path = None if plan_unreadable else scope_path or []
            # A copied tree can carry a .state directory in the shape of a scope. When the
            # run records plans, a real child scope's plan names its own path. A plan that
            # exists but cannot be read proves nothing either way, so its scope stays.
            if (
                parts
                and root_has_plan
                and not plan_unreadable
                and not _plan_names_scope(scope_path, parts, root_scope_path)
            ):
                ignored += 1
                continue
            status = _trimmed_status(_load_yaml_mapping(state_dir / paths_mod.PROCESS_STATUS_FILE))
            scopes.append(_Scope(scope_dir, tuple(parts), status, plan_steps))

        config = _load_yaml_mapping(run_dir / paths_mod.STATE_DIR / paths_mod.RUN_CONFIG_FILE)
        resource_summary: Mapping[str, Any] | None = None
        resource_summary_error: str | None = None
        summary_path = run_dir / RESOURCE_USAGE_SUMMARY_FILE
        if summary_path.is_file():
            try:
                metadata = _read_frontmatter(summary_path)
                envelope = metadata.get("resource_usage") if isinstance(metadata, Mapping) else None
                if isinstance(envelope, Mapping):
                    resource_summary = envelope
                else:
                    resource_summary_error = "resource-usage-summary.md has no resource_usage"
            except (OSError, ValueError, yaml.YAMLError) as exc:
                resource_summary_error = _describe_error(summary_path, exc, run_dir)
        else:
            resource_summary_error = "resource-usage-summary.md is absent"

        evidence = cls(
            run_dir=run_dir,
            run_id=_run_id(run_dir, config, scopes[0] if scopes else None, resource_summary),
            config=config or {},
            resource_summary=resource_summary,
            resource_summary_error=resource_summary_error,
            scopes=scopes or [_Scope(run_dir, (), None, {})],
            root_plan_error=root_plan_error,
            ignored_state_dirs=ignored,
            unreadable_plans=unreadable_plans,
        )
        evidence.by_path = {scope.path: scope for scope in evidence.scopes}
        return evidence


def _plan_names_scope(
    scope_path: list[str] | None, parts: tuple[str, ...], root_scope_path: list[str] | None
) -> bool:
    """Return whether a scope's own plan records the path the run addresses it by.

    With the run's prefix unknown, a plan naming the scope's path relative to any root
    still identifies it; a copy's plan names the path of the tree it was copied from.
    """
    if scope_path is None:
        return False
    if root_scope_path is None:
        return len(scope_path) >= len(parts) and scope_path[len(scope_path) - len(parts) :] == [
            *parts
        ]
    return scope_path == [*root_scope_path, *parts]


_PLAN_STEP_KEYS = ("step_id", "mode", "task_shape", "item_keys")
_STATUS_KEYS = ("process", "state", "started_at", "completed_at")
_STATUS_STEP_KEYS = ("state", "started_at", "completed_at", "elapsed_s")


def _plan_steps(
    document: Mapping[str, Any] | None,
) -> tuple[dict[str, Mapping[str, Any]], list[str] | None]:
    """Return each plan step's fields the fold reads, by step id, and the plan's scope path."""
    if document is None:
        raise ValueError("run-plan.yaml is absent")
    plan = document.get("run_plan", document)
    steps = plan.get("steps") if isinstance(plan, Mapping) else None
    if not isinstance(steps, list):
        raise ValueError("run-plan.yaml has no steps")
    raw_scope_path = plan.get("scope_path") if isinstance(plan, Mapping) else None
    scope_path = (
        [str(part) for part in raw_scope_path] if isinstance(raw_scope_path, list) else None
    )
    by_id: dict[str, Mapping[str, Any]] = {
        str(step["step_id"]): {key: step[key] for key in _PLAN_STEP_KEYS if key in step}
        for step in steps
        if isinstance(step, Mapping) and isinstance(step.get("step_id"), str)
    }
    return by_id, scope_path


def _trimmed_status(status: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """Keep the process status fields the fold reads, so large scope trees stay small."""
    if status is None:
        return None
    trimmed: dict[str, Any] = {key: status[key] for key in _STATUS_KEYS if key in status}
    steps = status.get("steps")
    if isinstance(steps, Mapping):
        trimmed["steps"] = {
            step_id: {key: entry[key] for key in _STATUS_STEP_KEYS if key in entry}
            for step_id, entry in steps.items()
            if isinstance(entry, Mapping)
        }
    return trimmed


def _run_id(
    run_dir: Path,
    config: Mapping[str, Any] | None,
    root: _Scope | None,
    resource_summary: Mapping[str, Any] | None,
) -> str:
    if resource_summary and isinstance(resource_summary.get("run_id"), str):
        return resource_summary["run_id"]
    if config:
        process = config.get("process")
        context = config.get("run_id")
        if isinstance(process, str) and isinstance(context, str) and process and context:
            return f"{process}/{context}"
    if root is not None and root.status and isinstance(root.status.get("process"), str):
        return f"{root.status['process']}/{run_dir.name}"
    return run_dir.name


# ── Sections ──────────────────────────────────────────────────────


def _run_figures(evidence: _Evidence, outcome: FinalizationState | None) -> RunFigures:
    unavailable: dict[str, str] = {}
    root = evidence.root
    config = evidence.config
    status = root.status

    process = _first_str(status.get("process") if status else None, config.get("process"))
    if process is None:
        unavailable["process"] = "neither process status nor run config names the process"

    state: str | None = None
    state_source: Literal["finalization", "resource_summary", "process_status"] | None = None
    finalization = (
        evidence.resource_summary.get("finalization") if evidence.resource_summary else None
    )
    finalized_state = (
        _first_str(finalization.get("state")) if isinstance(finalization, Mapping) else None
    )
    status_state = _first_str(status.get("state")) if status else None
    if outcome is not None:
        state, state_source = outcome.value, "finalization"
    elif finalized_state is not None:
        state, state_source = finalized_state, "resource_summary"
    elif status_state is not None:
        state, state_source = status_state, "process_status"
    else:
        unavailable["state"] = "no finalization outcome, resource summary, or process status"
        unavailable["state_source"] = unavailable["state"]

    started, ended = _status_window(status)
    elapsed_s: float | None = None
    if status is None:
        reason = ".state/process-status.yaml is absent at the run root"
        unavailable.update(started_at=reason, ended_at=reason, elapsed_s=reason)
    else:
        if started is None:
            unavailable["started_at"] = "process status records no start time"
        if ended is None:
            unavailable["ended_at"] = "process status records no completion time"
        if started is not None and ended is not None:
            elapsed_s = round((ended - started).total_seconds(), 3)
        else:
            unavailable["elapsed_s"] = "process status lacks a start or a completion time"

    item_count: int | None = None
    if root.plan_steps:
        keys = {key for step in _mapped_stage_steps(root) for key in step.get("item_keys") or []}
        item_count = len(keys)
    else:
        unavailable["item_count"] = evidence.root_plan_error or "root run plan is absent"

    variant = _first_str(config.get("variant"))
    execution_profile = _first_str(config.get("execution_profile"))
    backend = _first_str(config.get("backend"))
    no_config = "run config is absent" if not config else None
    if variant is None:
        unavailable["variant"] = no_config or "run config records no variant"
    if execution_profile is None:
        unavailable["execution_profile"] = no_config or "run config records no execution profile"
    if backend is None:
        unavailable["backend"] = no_config or "run config records no backend"

    revisions = _revisions(config)
    if not revisions:
        unavailable["revisions"] = no_config or "run config records no git_sha or *_REVISION"

    return RunFigures(
        process=process,
        state=state,
        state_source=state_source,
        started_at=_iso(started),
        ended_at=_iso(ended),
        elapsed_s=elapsed_s,
        item_count=item_count,
        variant=variant,
        execution_profile=execution_profile,
        backend=backend,
        revisions=revisions or None,
        unavailable=unavailable,
    )


def _stage_rows(evidence: _Evidence, run_elapsed_s: float | None) -> list[StageRow]:
    root = evidence.root
    entries = root.step_entries()
    step_ids = list(root.plan_steps) or list(entries)
    rows: list[StageRow] = []
    for step_id in step_ids:
        unavailable: dict[str, str] = {}
        plan = root.plan_steps.get(step_id)
        entry = entries.get(step_id)
        mode = _first_str(plan.get("mode")) if plan else None
        task_shape = plan.get("task_shape") if plan else None
        if task_shape not in {"scalar", "mapped"}:
            task_shape = None
        item_keys = plan.get("item_keys") if plan else None
        item_count = len(item_keys) if isinstance(item_keys, list) else None
        if plan is None:
            reason = evidence.root_plan_error or "root run plan does not declare this step"
            unavailable.update(mode=reason, task_shape=reason, item_count=reason)
        elif mode is None:
            unavailable["mode"] = "run plan records no mode"
        if plan is not None and task_shape is None:
            unavailable["task_shape"] = "run plan records no task shape"
        if plan is not None and item_count is None:
            unavailable["item_count"] = "run plan records no item keys"

        state = _first_str(entry.get("state")) if entry else None
        started = _parse_ts(entry.get("started_at")) if entry else None
        completed = _parse_ts(entry.get("completed_at")) if entry else None
        elapsed = _entry_elapsed(entry)
        not_run = "process status has no entry for this step"
        if entry is None:
            unavailable.update(
                state=not_run, started_at=not_run, completed_at=not_run, elapsed_s=not_run
            )
        else:
            if state is None:
                unavailable["state"] = "process status records no state"
            if started is None:
                unavailable["started_at"] = "step has not started"
            if completed is None:
                unavailable["completed_at"] = f"step is {state or 'not complete'}"
            if elapsed is None:
                unavailable["elapsed_s"] = f"step is {state or 'not complete'}"

        share: float | None = None
        if elapsed is not None and run_elapsed_s:
            share = round(elapsed / run_elapsed_s, 4)
        else:
            unavailable["share_of_elapsed"] = (
                "run elapsed is unavailable" if not run_elapsed_s else unavailable["elapsed_s"]
            )
        rows.append(
            StageRow(
                step_id=step_id,
                mode=mode,
                task_shape=task_shape,
                state=state,
                item_count=item_count,
                started_at=_iso(started),
                completed_at=_iso(completed),
                elapsed_s=elapsed,
                share_of_elapsed=share,
                unavailable=unavailable,
            )
        )
    return rows


def _setup_figures(
    evidence: _Evidence,
    stages: list[StageRow] | None,
    run_elapsed_s: float | None,
) -> SetupFigures | None:
    if stages is None:
        raise ValueError("stage rows are unavailable")
    if any(row.task_shape is None for row in stages):
        raise ValueError(
            evidence.root_plan_error or "a top-level step has no task shape in the run plan"
        )
    setup_rows = [row for row in stages if row.task_shape != "mapped"]
    unavailable: dict[str, str] = {}
    timed = [row.elapsed_s for row in setup_rows if row.elapsed_s is not None]
    elapsed = round(sum(timed), 3) if len(timed) == len(setup_rows) else None
    if elapsed is None:
        unavailable["elapsed_s"] = "a setup step has no elapsed time"
    share: float | None = None
    if elapsed is not None and run_elapsed_s:
        share = round(elapsed / run_elapsed_s, 4)
    else:
        unavailable["share_of_elapsed"] = unavailable.get("elapsed_s", "run elapsed is unavailable")
    return SetupFigures(
        step_ids=[row.step_id for row in setup_rows],
        elapsed_s=elapsed,
        share_of_elapsed=share,
        unavailable=unavailable,
    )


@dataclass
class _ItemRun:
    state: str | None
    started: datetime | None
    completed: datetime | None
    child_steps: dict[str, float]
    child_scope: _Scope | None


def _item_figures(evidence: _Evidence) -> ItemFigures | None:
    root = evidence.root
    if not root.plan_steps:
        raise ValueError(evidence.root_plan_error or "root run plan is absent")
    stage_steps = _mapped_stage_steps(root)
    if not stage_steps:
        raise ValueError("the run root has no mapped top-level step with items")
    stages = [str(step["step_id"]) for step in stage_steps]
    keys: list[str] = []
    for step in stage_steps:
        for key in step.get("item_keys") or []:
            if key not in keys:
                keys.append(key)

    runs: dict[tuple[str, str], _ItemRun] = {}
    for step in stage_steps:
        stage = str(step["step_id"])
        for key in step.get("item_keys") or []:
            runs[(stage, key)] = _item_run(evidence, root, stage, key)

    chains = [_item_chain(key, stages, runs) for key in keys]

    per_stage: list[StageItemStats] = []
    for stage in stages:
        stage_runs = [runs[(stage, key)] for key in keys if (stage, key) in runs]
        running = [
            (run.completed - run.started).total_seconds()
            for run in stage_runs
            if run.started is not None and run.completed is not None
        ]
        by_step: dict[str, list[float]] = defaultdict(list)
        modes: dict[str, str | None] = {}
        for run in stage_runs:
            for step_id, elapsed in run.child_steps.items():
                by_step[step_id].append(elapsed)
                if run.child_scope is not None and step_id not in modes:
                    modes[step_id] = _first_str(
                        run.child_scope.plan_steps.get(step_id, {}).get("mode")
                    )
        stats_unavailable: dict[str, str] = {}
        distribution = _distribution(running)
        if distribution is None:
            stats_unavailable["running_s"] = "no item in this stage has both a start and an end"
        per_stage.append(
            StageItemStats(
                stage=stage,
                item_count=len(stage_runs),
                running_s=distribution,
                steps=[
                    StageStepStats(step_id=step_id, mode=modes.get(step_id), elapsed_s=dist)
                    for step_id, values in by_step.items()
                    if (dist := _distribution(values)) is not None
                ],
                unavailable=stats_unavailable,
            )
        )

    unavailable: dict[str, str] = {}
    running_dist = _distribution(
        [c.chain_running_s for c in chains if c.chain_running_s is not None]
    )
    wait_dist = _distribution([c.barrier_wait_s for c in chains if c.barrier_wait_s is not None])
    span_dist = _distribution([c.chain_span_s for c in chains if c.chain_span_s is not None])
    if running_dist is None:
        unavailable["chain_running_s"] = "no item has a running time in every stage it entered"
    if wait_dist is None:
        unavailable["barrier_wait_s"] = "no item passed between two mapped stages"
    if span_dist is None:
        unavailable["chain_span_s"] = "no item has both a first start and a last completion"

    ranked = sorted(
        (c for c in chains if c.chain_running_s is not None),
        key=lambda c: c.chain_running_s or 0.0,
        reverse=True,
    )
    return ItemFigures(
        stages=stages,
        item_count=len(keys),
        chain_running_s=running_dist,
        barrier_wait_s=wait_dist,
        chain_span_s=span_dist,
        per_stage=per_stage,
        slowest=ranked[:SLOWEST_ITEM_COUNT],
        items=chains,
        unavailable=unavailable,
    )


def _item_run(evidence: _Evidence, scope: _Scope, step_id: str, item_key: str) -> _ItemRun:
    """Return one mapped item's run, read once per scope, step, and item."""
    memo_key = (scope.path, step_id, item_key)
    if (cached := evidence.item_runs.get(memo_key)) is not None:
        return cached
    run = _read_item_run(evidence, scope, step_id, item_key)
    evidence.item_runs[memo_key] = run
    return run


def _read_item_run(evidence: _Evidence, scope: _Scope, step_id: str, item_key: str) -> _ItemRun:
    child_dir = scope.path / step_id / item_key
    child = evidence.by_path.get(child_dir)
    if child is not None and child.status is not None:
        started, last_completion = _status_window(child.status)
        state = _first_str(child.status.get("state"))
        # A running scope has no end yet; a failed one ends at its last step completion.
        completed = None if state in {None, "running", "pending"} else last_completion
        steps = {
            sid: elapsed
            for sid, entry in child.step_entries().items()
            if (elapsed := _entry_elapsed(entry)) is not None
        }
        return _ItemRun(state, started, completed, steps, child)
    task_status = _load_yaml_mapping(
        scope.path / paths_mod.STATE_DIR / _TASKS / step_id / item_key / paths_mod.STATUS_FILE
    )
    if task_status is None:
        return _ItemRun(None, None, None, {}, None)
    return _ItemRun(
        _first_str(task_status.get("state")),
        _parse_ts(task_status.get("started_at")),
        _parse_ts(task_status.get("completed_at")),
        {},
        None,
    )


def _item_chain(
    key: str, stages: Sequence[str], runs: Mapping[tuple[str, str], _ItemRun]
) -> ItemChain:
    stage_rows: list[ItemStage] = []
    previous: _ItemRun | None = None
    running_values: list[float] = []
    waits: list[float] = []
    missing_running = False
    missing_wait = False
    overlap = False
    slowest: StepRef | None = None
    first_started: datetime | None = None
    last_completed: datetime | None = None

    # Mapped stages without an edge between them run in any order and can overlap, so an
    # item's stages pair in start order. Stages that never started follow in plan order.
    entered = [(stage, run) for stage in stages if (run := runs.get((stage, key))) is not None]
    entered.sort(key=lambda pair: (pair[1].started is None, pair[1].started or _NEVER))
    for stage, run in entered:
        unavailable: dict[str, str] = {}
        running: float | None = None
        if run.started is not None and run.completed is not None:
            running = round((run.completed - run.started).total_seconds(), 3)
            running_values.append(running)
        else:
            missing_running = True
            unavailable["running_s"] = (
                "item has no recorded state in this stage"
                if run.state is None
                else f"item is {run.state} without a start and an end"
            )
        wait: float | None = None
        if previous is None:
            unavailable["wait_before_s"] = "first mapped stage for this item"
        elif previous.completed is None or run.started is None:
            missing_wait = True
            unavailable["wait_before_s"] = (
                "previous stage completion or this stage start is missing"
            )
        elif run.started < previous.completed:
            overlap = True
            unavailable["wait_before_s"] = "this stage started before the previous stage completed"
        else:
            wait = round((run.started - previous.completed).total_seconds(), 3)
            waits.append(wait)
        stage_slowest: StepRef | None = None
        if run.child_steps:
            step_id, elapsed = max(run.child_steps.items(), key=lambda pair: pair[1])
            stage_slowest = StepRef(stage=stage, step_id=step_id, elapsed_s=elapsed)
            if slowest is None or elapsed > slowest.elapsed_s:
                slowest = stage_slowest
        else:
            unavailable["slowest_step"] = (
                "stage item scope records no child steps"
                if run.child_scope is not None
                else "mapped leaf step has no child scope"
            )
        if run.started is not None and first_started is None:
            first_started = run.started
        if run.completed is not None and (last_completed is None or run.completed > last_completed):
            last_completed = run.completed
        for name, value in (
            ("state", run.state),
            ("started_at", run.started),
            ("completed_at", run.completed),
        ):
            if value is None:
                unavailable[name] = "not recorded for this item in this stage"
        stage_rows.append(
            ItemStage(
                stage=stage,
                state=run.state,
                started_at=_iso(run.started),
                completed_at=_iso(run.completed),
                running_s=running,
                wait_before_s=wait,
                slowest_step=stage_slowest,
                unavailable=unavailable,
            )
        )
        previous = run

    unavailable = {}
    chain_running = (
        round(sum(running_values), 3) if running_values and not missing_running else None
    )
    if chain_running is None:
        unavailable["chain_running_s"] = "a stage this item entered has no running time"
    barrier: float | None = None
    if len(stage_rows) <= 1:
        unavailable["barrier_wait_s"] = "item entered one mapped stage"
    elif overlap:
        unavailable["barrier_wait_s"] = "stages overlap for this item"
    elif missing_wait:
        unavailable["barrier_wait_s"] = "a stage boundary is missing a completion or a start"
    else:
        barrier = round(sum(waits), 3)
    span: float | None = None
    if first_started is not None and last_completed is not None:
        span = round((last_completed - first_started).total_seconds(), 3)
    else:
        unavailable["chain_span_s"] = "item has no first start or no last completion"
    if slowest is None:
        unavailable["slowest_step"] = "no stage scope for this item records child steps"
    return ItemChain(
        item_key=key,
        stages=stage_rows,
        chain_running_s=chain_running,
        barrier_wait_s=barrier,
        chain_span_s=span,
        slowest_step=slowest,
        unavailable=unavailable,
    )


def _step_figures(evidence: _Evidence) -> StepFigures:
    samples: dict[tuple[str, str], list[float]] = defaultdict(list)
    modes: dict[tuple[str, str], str | None] = {}
    for scope in evidence.scopes:
        process = scope.process
        for step_id, entry in scope.step_entries().items():
            plan = scope.plan_steps.get(step_id, {})
            key = (process, step_id)
            modes.setdefault(key, _first_str(plan.get("mode")))
            item_keys = plan.get("item_keys") if plan.get("task_shape") == "mapped" else None
            if isinstance(item_keys, list) and item_keys:
                for item_key in item_keys:
                    run = _item_run(evidence, scope, step_id, str(item_key))
                    if run.started is not None and run.completed is not None:
                        samples[key].append((run.completed - run.started).total_seconds())
                continue
            elapsed = _entry_elapsed(entry)
            if elapsed is not None:
                samples[key].append(elapsed)

    rows = [
        StepTypeRow(
            process=process, step_id=step_id, mode=modes.get((process, step_id)), elapsed_s=dist
        )
        for (process, step_id), values in samples.items()
        if (dist := _distribution(values)) is not None
    ]
    rows.sort(key=lambda row: row.elapsed_s.total, reverse=True)
    unavailable: dict[str, str] = {}
    agent_total = sum(row.elapsed_s.total for row in rows if row.mode == "agent")
    code_total = sum(row.elapsed_s.total for row in rows if row.mode == "code")
    agent_rows = any(row.mode == "agent" for row in rows)
    code_rows = any(row.mode == "code" for row in rows)
    if not agent_rows:
        unavailable["agent_total_s"] = "no timed step has mode agent in its run plan"
    if not code_rows:
        unavailable["code_total_s"] = "no timed step has mode code in its run plan"
    return StepFigures(
        scope_count=len(evidence.scopes),
        ignored_state_dirs=evidence.ignored_state_dirs,
        unreadable_plans=evidence.unreadable_plans,
        rows=rows,
        agent_total_s=round(agent_total, 3) if agent_rows else None,
        code_total_s=round(code_total, 3) if code_rows else None,
        unavailable=unavailable,
    )


@dataclass
class _HealthSamples:
    by_stream_dir: dict[Path, list[Mapping[str, Any]]]

    def all(self) -> list[Mapping[str, Any]]:
        merged = [sample for samples in self.by_stream_dir.values() for sample in samples]
        merged.sort(key=lambda sample: str(sample.get("ts", "")))
        return merged


def _read_health_samples(evidence: _Evidence) -> _HealthSamples:
    by_dir: dict[Path, list[Mapping[str, Any]]] = {}
    for scope in evidence.scopes:
        for path in scope_runpool_health_files(scope.path):
            by_dir[path.parent] = [
                {key: obj.get(key) for key in _HEALTH_KEYS}
                for obj in iter_jsonl_objects(path)
                if obj.get("event") in {"health_sample", None}
            ]
    return _HealthSamples(by_dir)


_HEALTH_KEYS = (
    "ts",
    "active_count",
    "current_concurrency",
    "swap_used_gb",
    "swap_delta_gb_per_min",
    "disk_free_gb",
)


def _parallelism_figures(evidence: _Evidence, health: _HealthSamples | None) -> ParallelismFigures:
    """Fold the pool streams of the scopes the summary kept, so copied trees add nothing."""
    pools: list[PoolRow] = []
    empty_streams = 0
    for scope in evidence.scopes:
        for path in scope_runpool_event_files(scope.path):
            pool = _pool_row(evidence.run_dir, path, health)
            if pool is None:
                empty_streams += 1
            else:
                pools.append(pool)
    if not pools:
        raise ValueError(
            f"none of {empty_streams} RunPool event streams started a pool or a process"
            if empty_streams
            else "no RunPool event stream exists in any scope"
        )
    headline = max(
        pools, key=lambda pool: (pool.process_starts, pool.health_samples, pool.pressure_checks)
    )
    unavailable = {
        name: headline.unavailable[name]
        for name in (
            "ceiling",
            "peak_running",
            "mean_running",
            "sample_source",
            "share_samples_at_cap",
            "share_samples_at_ceiling",
        )
        if name in headline.unavailable
    }
    return ParallelismFigures(
        ceiling=headline.ceiling,
        peak_running=headline.peak_running,
        mean_running=headline.mean_running,
        health_samples=headline.health_samples,
        pressure_checks=headline.pressure_checks,
        sample_source=headline.sample_source,
        share_samples_at_cap=headline.share_samples_at_cap,
        share_samples_at_ceiling=headline.share_samples_at_ceiling,
        pools=pools,
        empty_streams=empty_streams,
        unavailable=unavailable,
    )


def _pool_row(run_dir: Path, events_path: Path, health: _HealthSamples | None) -> PoolRow | None:
    """Return the stream's concurrency row, or ``None`` when it started no pool or process."""
    pool_started = False
    ceiling: int | None = None
    starts = 0
    active: set[tuple[object, object]] = set()
    peak = 0
    weighted = 0.0
    window_start: datetime | None = None
    last_ts: datetime | None = None
    last_exit: datetime | None = None
    pressure_samples: list[Mapping[str, Any]] = []
    for event in iter_jsonl_objects(events_path):
        kind = event.get("event")
        ts = _parse_ts(event.get("ts"))
        if kind == "pool_start":
            pool_started = True
            maximum = event.get("max_concurrency")
            if isinstance(maximum, int):
                ceiling = max(ceiling or 0, maximum)
            continue
        if kind == "pressure_check":
            pressure_samples.append({key: event.get(key) for key in _HEALTH_KEYS})
            continue
        if kind not in {"process_start", "process_exit", "process_kill"} or ts is None:
            continue
        if last_ts is not None and window_start is not None and ts > last_ts:
            weighted += len(active) * (ts - last_ts).total_seconds()
        identity = (event.get("pid") or event.get("external_id"), event.get("label"))
        if kind == "process_start":
            starts += 1
            if window_start is None:
                window_start = ts
            active.add(identity)
            peak = max(peak, len(active))
        else:
            active.discard(identity)
            last_exit = ts
        if last_ts is None or ts > last_ts:
            last_ts = ts

    if not pool_started and not starts:
        return None
    unavailable: dict[str, str] = {}
    source = str(events_path.relative_to(run_dir))
    peak_running: int | None = peak if starts else None
    mean_running: float | None = None
    if not starts:
        unavailable["peak_running"] = "the pool recorded no process_start event"
        unavailable["mean_running"] = unavailable["peak_running"]
    elif window_start is None or last_exit is None or last_exit <= window_start:
        unavailable["mean_running"] = "the pool recorded no exit after its first start"
    else:
        mean_running = round(weighted / (last_exit - window_start).total_seconds(), 3)
    if ceiling is None:
        unavailable["ceiling"] = "the pool recorded no pool_start with max_concurrency"

    health_samples = (health.by_stream_dir.get(events_path.parent) if health else None) or []
    sample_source: Literal["health_sample", "pressure_check"] | None = None
    samples: list[Mapping[str, Any]] = []
    if health_samples:
        sample_source, samples = "health_sample", health_samples
    elif pressure_samples:
        sample_source, samples = "pressure_check", pressure_samples
    else:
        unavailable["sample_source"] = "the pool recorded no health or pressure samples"
    at_cap = [
        sample
        for sample in samples
        if isinstance(sample.get("active_count"), int)
        and isinstance(sample.get("current_concurrency"), int)
        and sample["current_concurrency"] > 0
        and sample["active_count"] >= sample["current_concurrency"]
    ]
    caps = [
        s["current_concurrency"] for s in samples if isinstance(s.get("current_concurrency"), int)
    ]
    share_at_cap: float | None = round(len(at_cap) / len(samples), 4) if samples else None
    share_at_ceiling: float | None = None
    if samples and ceiling:
        reached = [
            s
            for s in samples
            if isinstance(s.get("active_count"), int) and s["active_count"] >= ceiling
        ]
        share_at_ceiling = round(len(reached) / len(samples), 4)
    if not samples:
        unavailable["share_samples_at_cap"] = "the pool recorded no health or pressure samples"
        unavailable["share_samples_at_ceiling"] = unavailable["share_samples_at_cap"]
    elif share_at_ceiling is None:
        unavailable["share_samples_at_ceiling"] = "the pool ceiling is unknown"
    if not caps:
        reason = "no sample records current_concurrency"
        unavailable.update(cap_min=reason, cap_max=reason)
    return PoolRow(
        source=source,
        ceiling=ceiling,
        process_starts=starts,
        peak_running=peak_running,
        mean_running=mean_running,
        health_samples=len(health_samples),
        pressure_checks=len(pressure_samples),
        sample_source=sample_source,
        share_samples_at_cap=share_at_cap,
        share_samples_at_ceiling=share_at_ceiling,
        cap_min=min(caps) if caps else None,
        cap_max=max(caps) if caps else None,
        unavailable=unavailable,
    )


def _retry_figures(evidence: _Evidence) -> RetryFigures:
    by_disposition: Counter[str] = Counter()
    by_failure_class: Counter[str] = Counter()
    shapes: Counter[str] = Counter()
    step_attempts: Counter[tuple[str, str]] = Counter()
    step_failures: Counter[tuple[str, str]] = Counter()
    step_classes: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    tasks: set[tuple[Path, tuple[str, ...]]] = set()
    records = 0
    live = 0
    unreadable = 0
    non_record_files = 0
    failed_with_outputs = 0
    wrote_nothing = 0
    failed_total = 0

    for scope in evidence.scopes:
        tasks_dir = scope.path / paths_mod.STATE_DIR / _TASKS
        for path in _iter_named_files(tasks_dir, paths_mod.ATTEMPT_FILE, max_depth=5):
            try:
                loaded = _load_yaml_mapping(path, strict=True)
            except (OSError, ValueError, yaml.YAMLError):
                unreadable += 1
                continue
            record: Mapping[str, Any] = loaded or {}
            schema = record.get("schema")
            if not (isinstance(schema, str) and schema.startswith(_ATTEMPT_RECORD_SCHEMA_PREFIX)):
                non_record_files += 1
                continue
            records += 1
            relative = path.relative_to(tasks_dir).parts
            shapes[_attempt_shape(scope, relative)] += 1
            tasks.add((scope.path, _attempt_task(relative)))
            step_key = (scope.process, str(record.get("step_id") or relative[0]))
            step_attempts[step_key] += 1
            disposition = record.get("disposition")
            if not isinstance(disposition, str):
                live += 1
                continue
            by_disposition[disposition] += 1
            if disposition == "succeeded":
                continue
            failed_total += 1
            step_failures[step_key] += 1
            failure_class = record.get("failure_class")
            label = (
                failure_class
                if isinstance(failure_class, str) and failure_class
                else "unclassified"
            )
            by_failure_class[label] += 1
            step_classes[step_key][label] += 1
            failures = record.get("output_failures")
            if isinstance(failures, list) and failures:
                failed_with_outputs += 1
                if all(isinstance(f, Mapping) and f.get("kind") == "missing" for f in failures):
                    wrote_nothing += 1

    unavailable: dict[str, str] = {}
    wrote_nothing_value: int | None = wrote_nothing
    if failed_total and not failed_with_outputs:
        wrote_nothing_value = None
        unavailable["wrote_nothing"] = "no failed attempt recorded output failures"
    return RetryFigures(
        attempt_records=records,
        retries=records - len(tasks),
        by_disposition=dict(sorted(by_disposition.items())),
        by_failure_class=dict(sorted(by_failure_class.items())),
        live_attempts=live,
        wrote_nothing=wrote_nothing_value,
        by_step=[
            RetryStepRow(
                process=key[0],
                step_id=key[1],
                attempts=step_attempts[key],
                not_succeeded=count,
                by_failure_class=dict(sorted(step_classes[key].items())),
            )
            for key, count in sorted(step_failures.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
        path_shapes=dict(sorted(shapes.items())),
        unreadable_records=unreadable,
        non_record_attempt_files=non_record_files,
        unavailable=unavailable,
    )


def _attempt_shape(scope: _Scope, relative_parts: tuple[str, ...]) -> str:
    """Classify ``<step>/attempts/<id>/attempt.yaml`` against ``<step>/<item>/attempts/...``."""
    where = "root" if not scope.parts else "child"
    return f"{where}_{_attempt_kind(relative_parts)}"


def _attempt_task(relative_parts: tuple[str, ...]) -> tuple[str, ...]:
    """Return the step, or step and item, an attempt record belongs to."""
    kind = _attempt_kind(relative_parts)
    if kind == "step":
        return relative_parts[:1]
    if kind == "item":
        return relative_parts[:2]
    return relative_parts[:-1]


def _attempt_kind(relative_parts: tuple[str, ...]) -> Literal["step", "item", "other"]:
    if len(relative_parts) >= 4 and relative_parts[1] == paths_mod.ATTEMPTS_SUBDIR:
        return "step"
    if len(relative_parts) >= 5 and relative_parts[2] == paths_mod.ATTEMPTS_SUBDIR:
        return "item"
    return "other"


@dataclass
class _TranscriptReading:
    result: Mapping[str, Any] | None
    capped_tool_results: int


def _agent_figures(evidence: _Evidence) -> AgentFigures:
    transcripts = 0
    without_result = 0
    provider_ms = 0.0
    timed = 0
    requested: Counter[str] = Counter()
    served: Counter[str] = Counter()
    served_known = 0
    mismatches = 0
    compared = 0
    capped = 0
    oversized: list[str] = []
    profiles = _profile_models(evidence.config)

    for scope in evidence.scopes:
        logs_dir = scope.path / paths_mod.LOGS_DIR / _TASKS
        for path in _iter_transcripts(logs_dir):
            transcripts += 1
            logical_size = _logical_size(path)
            reading = _read_transcript(path, logical_size)
            capped += reading.capped_tool_results
            if logical_size is not None and logical_size >= TOOL_RESULT_CAP_BYTES:
                oversized.append(str(path.relative_to(evidence.run_dir)))
            requested_model = _requested_model(path, profiles)
            if requested_model:
                requested[requested_model] += 1
            if reading.result is None:
                without_result += 1
                continue
            duration = _result_duration_ms(reading.result)
            if duration is not None:
                provider_ms += duration
                timed += 1
            models = _result_models(reading.result)
            if models:
                served_known += 1
                for model in models:
                    served[model] += 1
                if requested_model:
                    compared += 1
                    if requested_model not in models:
                        mismatches += 1

    unavailable: dict[str, str] = {}
    if not transcripts:
        reason = "no agent transcript exists under any scope's .logs/tasks"
        unavailable.update(provider_s=reason, requested_models=reason, served_models=reason)
        unavailable["model_mismatches"] = reason
    else:
        if not timed:
            unavailable["provider_s"] = "no transcript terminal result reports a duration"
        if not requested:
            unavailable["requested_models"] = "no invocation record or profile names a model"
        if not served_known:
            unavailable["served_models"] = "no transcript terminal result names served models"
        if not compared:
            unavailable["model_mismatches"] = (
                "no invocation has both a requested and a served model"
            )

    tokens, meters, tool_calls = _token_figures(evidence)
    if tokens is None:
        unavailable["tokens"] = evidence.resource_summary_error or "resource summary has no totals"
    if tool_calls is None:
        unavailable["tool_calls"] = (
            evidence.resource_summary_error or "resource summary records no tool calls"
        )
    tool_results_at_cap: int | None = capped
    if not transcripts:
        tool_results_at_cap = None
        unavailable["tool_results_at_cap"] = "no agent transcript exists"
    return AgentFigures(
        transcripts=transcripts,
        transcripts_without_result=without_result,
        provider_s=round(provider_ms / 1000.0, 3) if timed else None,
        requested_models=dict(sorted(requested.items())) if requested else None,
        served_models=dict(sorted(served.items())) if served_known else None,
        model_mismatches=mismatches if compared else None,
        tokens=tokens,
        meters=meters,
        tool_calls=tool_calls,
        tool_results_at_cap=tool_results_at_cap,
        oversized_transcripts=oversized,
        unavailable=unavailable,
    )


def _token_figures(
    evidence: _Evidence,
) -> tuple[TokenFigures | None, list[MeterFigure], int | None]:
    summary = evidence.resource_summary
    totals = summary.get("totals") if summary else None
    if not isinstance(totals, Mapping):
        return None, [], None
    unavailable: dict[str, str] = {}
    values: dict[str, int | None] = {}
    for name in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
        value = totals.get(name)
        values[name] = int(value) if isinstance(value, (int, float)) else None
        if values[name] is None:
            unavailable[name] = "resource summary reports this total as unmeasured"
    meters: list[MeterFigure] = []
    raw_meters = summary.get("provider_meters") if summary else None
    for raw in raw_meters if isinstance(raw_meters, list) else []:
        if not isinstance(raw, Mapping):
            continue
        meters.append(
            MeterFigure.model_validate(
                {
                    key: raw.get(key)
                    for key in (
                        "key",
                        "coverage",
                        "actual_quantity",
                        "estimated_quantity",
                        "unmeasured_event_count",
                    )
                    if raw.get(key) is not None
                }
            )
        )
    tool_calls = totals.get("tool_calls")
    return (
        TokenFigures(
            input_tokens=values["input_tokens"],
            output_tokens=values["output_tokens"],
            cache_read_tokens=values["cache_read_tokens"],
            cache_write_tokens=values["cache_write_tokens"],
            unavailable=unavailable,
        ),
        meters,
        int(tool_calls) if isinstance(tool_calls, (int, float)) else None,
    )


def _resource_figures(
    evidence: _Evidence,
    health: _HealthSamples | None,
    health_error: str | None,
) -> ResourceFigures:
    unavailable: dict[str, str] = {}
    summary = evidence.resource_summary
    totals = summary.get("totals") if summary else None
    totals = totals if isinstance(totals, Mapping) else {}
    missing_summary = evidence.resource_summary_error or "resource summary has no totals"

    def total(name: str) -> float | None:
        value = _as_float(totals.get(name))
        if value is None:
            unavailable[name] = (
                missing_summary if not totals else "resource summary reports it unmeasured"
            )
        return value

    finalization = summary.get("finalization") if summary else None
    finalization_state = (
        _first_str(finalization.get("state")) if isinstance(finalization, Mapping) else None
    )
    if finalization_state is None:
        unavailable["resource_finalization_state"] = (
            evidence.resource_summary_error or "resource summary records no finalization"
        )

    list_cost = total("list_cost_usd")
    raw_unpriced = summary.get("unpriced_models") if summary else None
    unpriced_models = [
        UnpricedModel.model_validate(entry)
        for entry in (raw_unpriced if isinstance(raw_unpriced, list) else [])
        if isinstance(entry, Mapping)
    ]
    actual_cost = total("actual_cost_usd")
    cpu_avg = total("cpu_pct_avg")
    cpu_max = total("cpu_pct_max")
    rss_max = total("rss_bytes_max")

    samples = health.all() if health else []
    swap = _sample_floats(samples, "swap_used_gb")
    swap_delta = _sample_floats(samples, "swap_delta_gb_per_min")
    disk = _sample_floats(samples, "disk_free_gb")
    no_health = (
        f"RunPool health samples could not be read: {health_error}"
        if health_error
        else "no RunPool health sample exists in any scope"
    )
    if not swap:
        unavailable["swap_used_peak_gb"] = no_health if not samples else "no sample records swap"
        unavailable["swap_growth_gb"] = unavailable["swap_used_peak_gb"]
    if not swap_delta:
        unavailable["swap_delta_max_gb_per_min"] = (
            no_health if not samples else "no sample records a swap rate"
        )
    if not disk:
        unavailable["disk_free_min_gb"] = (
            no_health if not samples else "no sample records free disk"
        )

    size_bytes, file_count, walk_reason = _run_size(evidence.run_dir)
    if walk_reason is not None:
        unavailable["run_size_bytes"] = walk_reason
        unavailable["run_file_count"] = walk_reason

    return ResourceFigures(
        resource_finalization_state=finalization_state,
        list_cost_usd=round(list_cost, 6) if list_cost is not None else None,
        unpriced_models=unpriced_models,
        actual_cost_usd=round(actual_cost, 6) if actual_cost is not None else None,
        cpu_pct_avg=round(cpu_avg, 3) if cpu_avg is not None else None,
        cpu_pct_max=round(cpu_max, 3) if cpu_max is not None else None,
        rss_bytes_max=int(rss_max) if rss_max is not None else None,
        health_samples=len(samples),
        swap_used_peak_gb=round(max(swap), 3) if swap else None,
        swap_growth_gb=round(max(swap) - swap[0], 3) if swap else None,
        swap_delta_max_gb_per_min=round(max(swap_delta), 3) if swap_delta else None,
        disk_free_min_gb=round(min(disk), 3) if disk else None,
        run_size_bytes=size_bytes,
        run_file_count=file_count,
        unavailable=unavailable,
    )


# ── Transcripts ───────────────────────────────────────────────────


def _iter_transcripts(logs_tasks_dir: Path) -> Iterator[Path]:
    """Yield agent stream transcripts, preferring a plain file over its ``.gz`` sibling."""
    seen: set[str] = set()
    for path in sorted(_iter_files(logs_tasks_dir, max_depth=4)):
        name = path.name
        if name.endswith(".jsonl"):
            logical = str(path)
        elif name.endswith(".jsonl.gz"):
            logical = str(path)[: -len(".gz")]
        else:
            continue
        if logical in seen:
            continue
        if name.endswith(".gz") and Path(logical).is_file():
            continue
        seen.add(logical)
        yield path


def _logical_size(path: Path) -> int | None:
    try:
        return ArtifactPath(path).logical_size
    except OSError:
        return None


def _read_transcript(path: Path, logical_size: int | None) -> _TranscriptReading:
    """Return the terminal result and the count of capped tool-result lines."""
    is_gzip = path.name.endswith(".gz")
    needs_scan = is_gzip or (logical_size is not None and logical_size >= TOOL_RESULT_CAP_BYTES)
    if not needs_scan:
        result = _tail_result(path)
        if result is not None or (logical_size or 0) <= TRANSCRIPT_TAIL_BYTES:
            return _TranscriptReading(result, 0)
    try:
        if is_gzip:
            with gzip.open(path, "rb") as stream:
                return _scan_stream(stream)
        with path.open("rb") as stream:
            return _scan_stream(stream)
    except (OSError, EOFError, gzip.BadGzipFile):
        return _TranscriptReading(None, 0)


def _tail_result(path: Path) -> Mapping[str, Any] | None:
    try:
        with path.open("rb") as stream:
            size = stream.seek(0, os.SEEK_END)
            offset = max(0, size - TRANSCRIPT_TAIL_BYTES)
            stream.seek(offset)
            data = stream.read()
    except OSError:
        return None
    lines = data.split(b"\n")
    if offset > 0:
        lines = lines[1:]
    for line in reversed(lines):
        result = _result_from_line(line)
        if result is not None:
            return result
    return None


class _ByteReader(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


def _scan_stream(stream: _ByteReader) -> _TranscriptReading:
    """Scan JSONL in fixed chunks, keeping only result lines and long-line prefixes."""
    result: Mapping[str, Any] | None = None
    capped = 0
    length = 0
    prefix = bytearray()
    kept = bytearray()
    keeping = True

    def finish_line() -> None:
        nonlocal result, capped
        if length >= TOOL_RESULT_CAP_BYTES and any(m in prefix for m in _TOOL_RESULT_MARKERS):
            capped += 1
        if keeping and kept:
            parsed = _result_from_line(bytes(kept))
            if parsed is not None:
                result = parsed

    while chunk := stream.read(SCAN_CHUNK_BYTES):
        start = 0
        while start < len(chunk):
            newline = chunk.find(b"\n", start)
            end = len(chunk) if newline < 0 else newline
            piece = chunk[start:end]
            if len(prefix) < LINE_PREFIX_BYTES:
                prefix.extend(piece[: LINE_PREFIX_BYTES - len(prefix)])
            if (
                keeping
                and len(prefix) >= _RESULT_MARKER_WINDOW
                and not any(m in prefix[:_RESULT_MARKER_WINDOW] for m in _RESULT_MARKERS)
            ):
                keeping = False
                kept.clear()
            length += len(piece)
            if keeping:
                if length > RESULT_LINE_MAX_BYTES:
                    keeping = False
                    kept.clear()
                else:
                    kept.extend(piece)
            if newline < 0:
                break
            finish_line()
            length = 0
            prefix.clear()
            kept.clear()
            keeping = True
            start = newline + 1
    if length:
        finish_line()
    return _TranscriptReading(result, capped)


def _result_from_line(line: bytes) -> Mapping[str, Any] | None:
    stripped = line.strip()
    if not stripped.startswith(b"{") or not any(
        m in stripped[:_RESULT_MARKER_WINDOW] for m in _RESULT_MARKERS
    ):
        return None
    try:
        parsed = json.loads(stripped)
    except ValueError:
        return None
    if isinstance(parsed, dict) and parsed.get("type") == "result":
        return parsed
    return None


def _result_duration_ms(result: Mapping[str, Any]) -> float | None:
    stats = result.get("stats")
    if isinstance(stats, Mapping) and (value := _as_float(stats.get("duration_ms"))) is not None:
        return value
    return _as_float(result.get("duration_ms"))


def _result_models(result: Mapping[str, Any]) -> list[str]:
    stats = result.get("stats")
    models = stats.get("models") if isinstance(stats, Mapping) else None
    if isinstance(models, Mapping):
        return [str(model) for model in models]
    usage = result.get("modelUsage")
    if isinstance(usage, Mapping):
        return [str(model) for model in usage]
    return []


def _requested_model(transcript: Path, profiles: Mapping[str, str]) -> str | None:
    sidecar = artifact_sidecar_path(transcript, ".jsonl.invocation.json")
    try:
        invocation = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(invocation, Mapping):
        return None
    argv = invocation.get("argv")
    if isinstance(argv, list):
        for index, arg in enumerate(argv[:-1]):
            if arg in _MODEL_FLAGS and isinstance(argv[index + 1], str):
                return argv[index + 1]
            if isinstance(arg, str) and arg.startswith("--model="):
                return arg.split("=", 1)[1]
    metadata = invocation.get("metadata")
    profile = metadata.get("execution_profile") if isinstance(metadata, Mapping) else None
    return profiles.get(profile) if isinstance(profile, str) else None


def _profile_models(config: Mapping[str, Any]) -> dict[str, str]:
    models: dict[str, str] = {}
    for profile in config.get("resolved_profiles") or []:
        if not isinstance(profile, Mapping):
            continue
        name = profile.get("name")
        profile_config = profile.get("config")
        model = profile_config.get("model") if isinstance(profile_config, Mapping) else None
        if isinstance(name, str) and isinstance(model, str):
            models[name] = model
    return models


# ── Health and disk ───────────────────────────────────────────────


def _run_size(run_dir: Path) -> tuple[int | None, int | None, str | None]:
    """Sum allocated bytes with an explicit entry bound; never follow symlinks."""
    total = 0
    files = 0
    entries = 0
    stack = [run_dir]
    while stack:
        directory = stack.pop()
        try:
            iterator = os.scandir(directory)
        except OSError as exc:
            return None, None, f"could not list {directory.name}: {exc.strerror or exc}"
        with iterator:
            for entry in iterator:
                entries += 1
                if entries > RUN_SIZE_WALK_LIMIT:
                    return None, None, f"walk stopped after {RUN_SIZE_WALK_LIMIT} entries"
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                        continue
                    stat = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                files += 1
                blocks = getattr(stat, "st_blocks", None)
                total += blocks * 512 if isinstance(blocks, int) else stat.st_size
    return total, files, None


# ── Small helpers ─────────────────────────────────────────────────


def _contained[T](section: str, unavailable: dict[str, str], build: Callable[[], T]) -> T | None:
    try:
        return build()
    except Exception as exc:  # noqa: BLE001 - one section's evidence never blocks the others
        message = str(exc) or type(exc).__name__
        unavailable[section] = (
            message if isinstance(exc, ValueError) else f"{type(exc).__name__}: {message}"
        )
        if not isinstance(exc, ValueError):
            log.warning("operations summary section %s failed", section, exc_info=True)
        return None


def _mapped_stage_steps(scope: _Scope) -> list[Mapping[str, Any]]:
    return [
        step
        for step in scope.plan_steps.values()
        if step.get("task_shape") == "mapped" and step.get("item_keys")
    ]


def _status_window(status: Mapping[str, Any] | None) -> tuple[datetime | None, datetime | None]:
    """Return the first start and last completion across a status and its steps."""
    if status is None:
        return None, None
    starts = [_parse_ts(status.get("started_at"))]
    ends = [_parse_ts(status.get("completed_at"))]
    steps = status.get("steps")
    if isinstance(steps, Mapping):
        for entry in steps.values():
            if isinstance(entry, Mapping):
                starts.append(_parse_ts(entry.get("started_at")))
                ends.append(_parse_ts(entry.get("completed_at")))
    known_starts = [value for value in starts if value is not None]
    known_ends = [value for value in ends if value is not None]
    return (min(known_starts) if known_starts else None, max(known_ends) if known_ends else None)


def _entry_elapsed(entry: Mapping[str, Any] | None) -> float | None:
    if entry is None:
        return None
    completed = _parse_ts(entry.get("completed_at"))
    if completed is None:
        return None
    value = _as_float(entry.get("elapsed_s"))
    if value is not None:
        return value
    started = _parse_ts(entry.get("started_at"))
    if started is None:
        return None
    return round((completed - started).total_seconds(), 3)


def _distribution(values: Iterable[float]) -> Distribution | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    total = sum(ordered)
    return Distribution(
        count=len(ordered),
        p50=round(_percentile(ordered, 0.5), 3),
        p90=round(_percentile(ordered, 0.9), 3),
        max=round(ordered[-1], 3),
        mean=round(total / len(ordered), 3),
        total=round(total, 3),
    )


def _percentile(ordered: Sequence[float], quantile: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    position = quantile * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _parse_ts(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value is not None else None


def _first_str(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _sample_floats(samples: Iterable[Mapping[str, Any]], key: str) -> list[float]:
    return [value for sample in samples if (value := _as_float(sample.get(key))) is not None]


def _revisions(config: Mapping[str, Any]) -> dict[str, str]:
    revisions: dict[str, str] = {}
    git_sha = config.get("git_sha")
    if isinstance(git_sha, str) and git_sha:
        revisions["git_sha"] = git_sha
    variables = config.get("variables")
    if isinstance(variables, Mapping):
        seen: set[str] = set()
        for key in sorted(variables, key=lambda name: (not str(name).isupper(), str(name))):
            name = str(key)
            value = variables[key]
            if _REVISION_KEY.match(name) and isinstance(value, str) and value:
                folded = name.lower()
                if folded not in seen:
                    seen.add(folded)
                    revisions[name] = value
    return revisions


def _load_yaml_mapping(path: Path, *, strict: bool = False) -> Mapping[str, Any] | None:
    """Parse a YAML mapping; ``strict`` raises where the lenient form returns ``None``."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if strict:
            raise
        return None
    except OSError:
        if strict:
            raise
        return None
    try:
        loaded = yaml.load(text, Loader=_YAML_LOADER)  # noqa: S506 - safe loader
    except yaml.YAMLError:
        if strict:
            raise
        return None
    if isinstance(loaded, Mapping):
        return loaded
    if strict:
        raise ValueError(f"{path.name} is not a mapping")
    return None


def _read_frontmatter(path: Path) -> Mapping[str, Any] | None:
    text = path.read_text(encoding="utf-8")
    metadata, _offset, _start = fmf_split_frontmatter(text)
    if metadata is None:
        return None
    loaded = yaml.load(metadata, Loader=_YAML_LOADER)  # noqa: S506 - safe loader
    return loaded if isinstance(loaded, Mapping) else None


def _iter_files(root: Path, *, max_depth: int) -> Iterator[Path]:
    if not root.is_dir():
        return
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        directory, depth = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if depth < max_depth:
                        stack.append((Path(entry.path), depth + 1))
                elif entry.is_file(follow_symlinks=False):
                    yield Path(entry.path)
            except OSError:
                continue


def _iter_named_files(root: Path, name: str, *, max_depth: int) -> Iterator[Path]:
    for path in _iter_files(root, max_depth=max_depth):
        if path.name == name:
            yield path


def _describe_error(path: Path, exc: BaseException, run_dir: Path) -> str:
    try:
        shown = path.relative_to(run_dir)
    except ValueError:
        shown = path
    return f"{shown}: {_error_detail(exc)}"


def _error_detail(exc: BaseException) -> str:
    detail = exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc)
    return " ".join(detail.split()) or type(exc).__name__


def _write_schema_sidecar(target: Path) -> None:
    source = resources.files("metaproc").joinpath(
        "data", "schemas", "agent-operations-summary.v1.schema.yaml"
    )
    content = source.read_text(encoding="utf-8")
    atomic_write_text(target, content, make_parents=True)
