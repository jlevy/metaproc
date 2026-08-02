"""Provider-free terminal resource finalization and recovery."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from ruamel.yaml import YAMLError

from metaproc.engine.resource_rollup import (
    ResourceBuildResult,
    build_resource_artifacts,
    build_resource_artifacts_from_events,
    write_resource_artifacts,
)
from metaproc.ids import new_typed_id
from metaproc.io import atomic_output_file, iter_artifact_paths, read_yaml_file, to_yaml_string
from metaproc.logutil.resource_events import read_events
from metaproc.models.plan_bundle import PlanBundle
from metaproc.models.resource_budget import (
    FinalizationState,
    ResourceBudgetSpec,
    ResourceFinalization,
    ResourceUsageSummary,
    collect_resource_budgets,
    evaluate_resource_budgets,
)
from metaproc.models.resources import ResourcesDocument, read_resources_document_json
from metaproc.paths import (
    POOL_STATUS_FILE,
    STATUS_FILE,
    process_events_log,
    resource_usage_summary_file,
    resources_file,
    run_config_file,
    run_resource_events_file,
    run_state_dir,
)
from metaproc.plan_bundle_loader import load_plan_bundle
from metaproc.runpool.process_event_models import ProcessCompleteEvent, ProcessStartEvent
from metaproc.runpool.process_events import read_process_events
from metaproc.runpool.status import read_status as read_pool_status

log = logging.getLogger(__name__)

_DERIVED_USAGE_DIGEST_BYTES = 20


def state_for_terminal_error(error: BaseException | None) -> FinalizationState:
    """Map an orchestration outcome to the stable terminal report state."""
    if error is None:
        return FinalizationState.COMPLETED
    if isinstance(error, (subprocess.TimeoutExpired, TimeoutError)):
        return FinalizationState.TIMED_OUT
    if isinstance(error, (KeyboardInterrupt, asyncio.CancelledError)):
        return FinalizationState.CANCELLED
    return FinalizationState.FAILED


def finalize_run_resources(
    run_dir: Path,
    *,
    run_id: str,
    state: FinalizationState,
    trigger: Literal["terminal", "resume", "status", "rehydration"],
    bundle: PlanBundle | None = None,
    budgets: Sequence[ResourceBudgetSpec] | None = None,
    recovered: bool = False,
    terminal_error: BaseException | None = None,
) -> ResourcesDocument:
    """Build and persist terminal usage artifacts without provider calls.

    The projection is idempotent: when source evidence, terminal state, and
    budgets are unchanged, existing machine and human report bytes are kept.
    ``trigger`` records how a new projection was requested but does not itself
    make an otherwise-identical report change.
    """
    run_dir = run_dir.resolve()
    existing = _read_existing_document(resources_file(run_dir))
    result = _build_result(run_dir, run_id=run_id, bundle=bundle, existing=existing)
    effective_budgets = _resolve_budgets(
        budgets,
        bundle=bundle,
        existing=existing,
    )
    usage_id = (
        existing.usage_id
        if existing is not None and existing.usage_id is not None
        else _derived_usage_id(run_id)
    )
    state = _state_from_local_terminal_evidence(run_dir, state)
    terminal_error_type = type(terminal_error).__name__ if terminal_error is not None else None
    if (
        terminal_error_type is None
        and existing is not None
        and existing.finalization is not None
        and existing.finalization.state is state
    ):
        terminal_error_type = existing.finalization.terminal_error_type
    finalization = ResourceFinalization(
        state=state,
        trigger=trigger,
        finalized_at=datetime.now(UTC),
        recovered=recovered,
        source_event_count=len(result.events),
        terminal_error_type=terminal_error_type,
    )
    candidate = result.document.model_copy(
        update={
            "usage_id": usage_id,
            "budget_evaluations": evaluate_resource_budgets(
                result.document,
                effective_budgets,
                events=result.events,
            ),
            "finalization": finalization,
            "summary_path": resource_usage_summary_file(run_dir).name,
        }
    )
    result.document = candidate
    result.document_path = resources_file(run_dir)
    result.events_path = run_resource_events_file(run_dir)

    if existing is not None and _substantive_document(existing) == _substantive_document(candidate):
        result.document = existing
        if not result.events_path.exists():
            write_resource_artifacts(result)
        if not resource_usage_summary_file(run_dir).exists():
            _write_summary(run_dir, existing)
        return existing

    write_resource_artifacts(result)
    _write_summary(run_dir, candidate)
    return candidate


def recover_incomplete_resource_artifacts(
    run_dir: Path,
    *,
    is_active: bool,
) -> ResourcesDocument | None:
    """Recover missing terminal reports for an inactive run.

    Recovery reads only the run config and local event ledgers. It never calls
    an LLM, web-search API, Trends provider, or any other remote provider.
    """
    if is_active:
        return None
    machine_path = resources_file(run_dir)
    summary_path = resource_usage_summary_file(run_dir)
    outcome = _terminal_outcome_from_process_events(process_events_log(run_dir))
    if outcome is None:
        return None
    state, latest_lifecycle_at = outcome
    existing = _read_existing_document(machine_path)
    if machine_path.exists() and existing is None:
        log.warning("leaving unreadable or historical resource report untouched: %s", machine_path)
        return None
    if (
        existing is not None
        and existing.finalization is not None
        and existing.finalization.finalized_at >= latest_lifecycle_at
    ):
        if summary_path.exists():
            return None
        state = existing.finalization.state

    config = _read_run_config(run_dir)
    bundle = _bundle_from_run_config(config)
    budgets = _budgets_from_run_config(config)
    configured_run_id = config.get("run_id") if config is not None else None
    run_id = configured_run_id if isinstance(configured_run_id, str) else run_dir.name

    try:
        return finalize_run_resources(
            run_dir,
            bundle=bundle,
            run_id=run_id,
            state=state,
            trigger="status",
            budgets=budgets,
            recovered=True,
        )
    except Exception:  # noqa: BLE001 -- recovery must never break status
        log.warning("could not recover resource reports under %s", run_dir, exc_info=True)
        return None


def _build_result(
    run_dir: Path,
    *,
    run_id: str,
    bundle: PlanBundle | None,
    existing: ResourcesDocument | None,
) -> ResourceBuildResult:
    if bundle is not None:
        return build_resource_artifacts(
            bundle=bundle,
            run_dir=run_dir,
            run_id=run_id,
            source_events_path=".logs/resource-events.jsonl",
        )
    ledger_path = run_resource_events_file(run_dir)
    if existing is not None:
        events = read_events(ledger_path) if ledger_path.exists() else []
        return ResourceBuildResult(document=existing.model_copy(deep=True), events=events)
    if ledger_path.exists():
        events = read_events(ledger_path)
        return build_resource_artifacts_from_events(
            run_id=run_id,
            events=events,
            source_events_path=".logs/resource-events.jsonl",
        )
    raise ValueError("resource finalization requires a plan bundle or persisted resource evidence")


def _resolve_budgets(
    budgets: Sequence[ResourceBudgetSpec] | None,
    *,
    bundle: PlanBundle | None,
    existing: ResourcesDocument | None,
) -> list[ResourceBudgetSpec]:
    if budgets is not None:
        return [budget.model_copy(deep=True) for budget in budgets]
    if bundle is not None:
        return collect_resource_budgets(bundle.plan)
    if existing is not None:
        return [
            evaluation.budget.model_copy(deep=True) for evaluation in existing.budget_evaluations
        ]
    return []


def _read_existing_document(path: Path) -> ResourcesDocument | None:
    if not path.exists():
        return None
    try:
        document = read_resources_document_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return document if isinstance(document, ResourcesDocument) else None


def _substantive_document(document: ResourcesDocument) -> dict[str, object]:
    payload = cast(
        "dict[str, object]",
        document.model_dump(mode="json", by_alias=True),
    )
    payload.pop("generated_at", None)
    finalization = payload.get("finalization")
    if isinstance(finalization, dict):
        finalization.pop("finalized_at", None)
        finalization.pop("trigger", None)
        finalization.pop("recovered", None)
    return payload


def _derived_usage_id(run_id: str) -> str:
    digest = hashlib.sha256(run_id.encode()).digest()
    suffix = base64.b32encode(digest[:_DERIVED_USAGE_DIGEST_BYTES]).decode().lower().rstrip("=")
    return new_typed_id("use", unique_suffix=suffix)


def _state_from_local_terminal_evidence(
    run_dir: Path,
    state: FinalizationState,
) -> FinalizationState:
    """Refine generic failure from authoritative timeout/cancellation sidecars."""
    if state is not FinalizationState.FAILED:
        return state
    state_root = run_state_dir(run_dir)
    for path in iter_artifact_paths(state_root, f"**/{STATUS_FILE}"):
        try:
            raw = read_yaml_file(path)
        except (OSError, ValueError, YAMLError):
            continue
        if not isinstance(raw, dict) or raw.get("state") != "failed":
            continue
        error = raw.get("error")
        if isinstance(error, str) and _is_timeout_error(error):
            return FinalizationState.TIMED_OUT

    saw_cancel = False
    for path in iter_artifact_paths(state_root, f"**/{POOL_STATUS_FILE}"):
        try:
            status = read_pool_status(path)
        except (OSError, ValueError, YAMLError):
            continue
        if status.failure_counts.timeout > 0 or any(
            item.kill_reason == "timeout" for item in status.recent_completions
        ):
            return FinalizationState.TIMED_OUT
        if any(
            item.kill_reason in {"external_kill", "shutdown"} for item in status.recent_completions
        ):
            saw_cancel = True
    return FinalizationState.CANCELLED if saw_cancel else state


def _is_timeout_error(error: str) -> bool:
    normalized = error.casefold()
    return normalized.startswith("timeout after ") or any(
        marker in normalized for marker in ("timeouterror", "timeoutexpired")
    )


def _write_summary(run_dir: Path, document: ResourcesDocument) -> None:
    if document.usage_id is None or document.finalization is None:
        raise ValueError("terminal resource summary requires usage ID and finalization metadata")
    summary = ResourceUsageSummary(
        usage_id=document.usage_id,
        run_id=document.run_id,
        generated_at=document.generated_at,
        resource_schema=document.schema_version,
        finalization=document.finalization,
        metrics=cast(
            "dict[str, float | int | None]",
            document.hierarchy_root.total_metrics.model_dump(),
        ),
        provider_meters=[
            cast("dict[str, object]", meter.model_dump(mode="json"))
            for meter in document.meter_rollups
        ],
        budget_evaluations=document.budget_evaluations,
    )
    metadata = cast("dict[str, object]", summary.model_dump(mode="json", by_alias=True))
    rendered = f"---\n{to_yaml_string(metadata)}---\n{_summary_markdown(document)}"
    with atomic_output_file(resource_usage_summary_file(run_dir), make_parents=True) as tmp:
        tmp.write_text(rendered, encoding="utf-8")


def _summary_markdown(document: ResourcesDocument) -> str:
    finalization = document.finalization
    if finalization is None:
        raise ValueError("terminal summary requires finalization metadata")
    metrics = document.hierarchy_root.total_metrics
    lines = [
        "# Resource usage summary",
        "",
        f"Run `{document.run_id}` finalized as `{finalization.state.value}`.",
        "",
        "## Core usage",
        "",
        "| Metric | Quantity |",
        "| --- | ---: |",
    ]
    for name, value in metrics.model_dump().items():
        if value is not None:
            lines.append(f"| `{name}` | {value} |")

    lines.extend(["", "## Provider meters", ""])
    if document.meter_rollups:
        lines.extend(
            [
                "| Provider | Product | Meter | Unit | Coverage | Actual | Estimated |",
                "| --- | --- | --- | --- | --- | ---: | ---: |",
            ]
        )
        for rollup in document.meter_rollups:
            lines.append(
                "| "
                + " | ".join(
                    [
                        rollup.key.provider,
                        rollup.key.product,
                        rollup.key.meter,
                        rollup.key.unit,
                        rollup.coverage.value,
                        _quantity(rollup.actual_quantity),
                        _quantity(rollup.estimated_quantity),
                    ]
                )
                + " |"
            )
    else:
        lines.append("No provider meters were observed.")

    lines.extend(["", "## Budgets", ""])
    if document.budget_evaluations:
        lines.extend(
            [
                "| Budget | Scope | Target | Unit | Status | Coverage | Observed | Threshold |",
                "| --- | --- | --- | --- | --- | --- | ---: | ---: |",
            ]
        )
        for evaluation in document.budget_evaluations:
            budget = evaluation.budget
            target = (
                budget.metric.value
                if budget.metric is not None
                else "/".join(budget.meter.sort_key() if budget.meter is not None else ("meter",))
            )
            observed = (
                evaluation.actual_quantity
                if evaluation.actual_quantity is not None
                else evaluation.estimated_quantity
            )
            scope = budget.scope.kind.value
            if budget.scope.key:
                scope += f":{budget.scope.key}"
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{budget.budget_id}`",
                        scope,
                        target,
                        budget.unit or "?",
                        evaluation.status.value,
                        evaluation.coverage.value,
                        _quantity(observed),
                        str(budget.threshold),
                    ]
                )
                + " |"
            )
    else:
        lines.append("No resource budgets were declared.")
    lines.append("")
    return "\n".join(lines)


def _quantity(value: float | None) -> str:
    return "—" if value is None else f"{value:g}"


def _terminal_outcome_from_process_events(
    path: Path,
) -> tuple[FinalizationState, datetime] | None:
    if not path.exists():
        return None
    try:
        events = read_process_events(path)
    except (OSError, ValueError, YAMLError):
        return None
    lifecycle = [
        event for event in events if isinstance(event, (ProcessStartEvent, ProcessCompleteEvent))
    ]
    if not lifecycle:
        return None
    latest = lifecycle[-1]
    if isinstance(latest, ProcessStartEvent):
        return (FinalizationState.INTERRUPTED, latest.ts)
    state = FinalizationState.FAILED if latest.failed else FinalizationState.COMPLETED
    return (state, latest.ts)


def _read_run_config(run_dir: Path) -> dict[str, object] | None:
    try:
        raw = read_yaml_file(run_config_file(run_dir))
    except (OSError, ValueError, YAMLError):
        return None
    return cast("dict[str, object]", raw) if isinstance(raw, dict) else None


def _bundle_from_run_config(config: dict[str, object] | None) -> PlanBundle | None:
    if config is None:
        return None
    raw_path = config.get("process_spec")
    if not isinstance(raw_path, str) or not raw_path:
        return None
    raw_variables = config.get("variables")
    variables = (
        {
            str(key): value
            for key, value in raw_variables.items()
            if isinstance(key, str) and isinstance(value, str)
        }
        if isinstance(raw_variables, dict)
        else {}
    )
    try:
        return load_plan_bundle(Path(raw_path), params=variables, validate_spec=False)
    except Exception:  # noqa: BLE001 -- best-effort recovery from a moved spec
        log.info("could not reload process bundle from %s", raw_path, exc_info=True)
        return None


def _budgets_from_run_config(
    config: dict[str, object] | None,
) -> list[ResourceBudgetSpec] | None:
    if config is None or "resource_budgets" not in config:
        return None
    raw = config.get("resource_budgets")
    if not isinstance(raw, list):
        return None
    try:
        return [ResourceBudgetSpec.model_validate(item) for item in raw]
    except ValueError:
        log.warning("ignoring malformed resource_budgets in run config", exc_info=True)
        return None
