"""Lane matrix expansion — materialize execution lanes and task instances.

This module is the planner-side seam where a declarative ``LaneMatrix``
becomes a concrete list of ``ExecutionLane`` rows and per-step task
instances. It is consumed by :mod:`metaproc.engine.build_plan` during
plan resolution and by ``metaproc plan`` for dry-run rendering.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from metaproc.models.execution_profile import (
    ExecutionProfileRegistry,
    ResolvedExecutionProfile,
)
from metaproc.models.lane import (
    ExecutionLane,
    LaneMatrix,
    LaneSpec,
    TaskInstance,
    derive_lane_id,
    derive_task_id,
)
from metaproc.models.plan import ResolvedStep


def materialize_execution_lanes(
    lane_matrix: LaneMatrix | None,
    *,
    registry: ExecutionProfileRegistry,
    fallback_profile: ResolvedExecutionProfile | None = None,
    fallback_artifact_namespace: str | None = None,
) -> list[ExecutionLane]:
    """Materialize ``lane_matrix`` into resolved execution-lane rows.

    Each row is validated against the profile registry and assigned a
    stable ``lane_id``. Duplicate ids are rejected so downstream consumers
    can rely on lane ids being unique within a plan.

    When ``lane_matrix`` is ``None`` or empty, the caller's
    ``fallback_profile`` is used to synthesize a single degenerate lane —
    that's the one-profile, one-replica case that lets existing processes
    keep their pre-lanes behavior.
    """
    rows: list[ExecutionLane] = []
    seen_ids: set[str] = set()

    specs: Sequence[LaneSpec]
    if lane_matrix is None or not lane_matrix.lanes:
        if fallback_profile is None:
            return rows
        specs = [
            LaneSpec(
                execution_profile=fallback_profile.name,
                artifact_namespace=fallback_artifact_namespace,
            )
        ]
    else:
        specs = lane_matrix.lanes

    for spec in specs:
        # Lane-local config overrides hash into the derived lane id (so two
        # lanes that differ only by overrides get distinct ids), but the
        # execution path does not yet route per-lane adapter config — runpool
        # admission still uses RunPoolConfig.execution_profile and the shared
        # adapter config from the resolved step. Accepting non-empty overrides
        # silently would mislead operators: trace spans would group cleanly
        # by lane_id, but every lane would actually run with the same
        # adapter config. Refuse here and let it light up when the
        # same-runpool multi-lane drive (internal issue epic, follow-up to
        # internal issue) wires per-lane configs through to execution.
        if spec.overrides:
            raise ValueError(
                f"lane_matrix lane {spec.lane_id or spec.execution_profile!r}: "
                "lane-local overrides are not yet wired through to execution; "
                "every lane currently runs with the run-level adapter config. "
                "Remove the 'overrides' block until same-runpool multi-lane "
                "drive lands. See "
                "docs/project/specs/active/plan-2026-05-18-metaproc-execution-lanes-and-runpool.md "
                "§ Phase 2 follow-up."
            )
        resolved = _resolve_lane_profile(registry, spec)
        lane_id = spec.lane_id or derive_lane_id(
            execution_profile=spec.execution_profile,
            replica_index=spec.replica_index,
            overrides=spec.overrides,
        )
        if lane_id in seen_ids:
            raise ValueError(
                f"duplicate lane_id {lane_id!r} in lane_matrix; "
                "set distinct lane_id, replica_index, or overrides to differentiate"
            )
        seen_ids.add(lane_id)
        artifact_namespace = spec.artifact_namespace
        if artifact_namespace is None and fallback_artifact_namespace is not None:
            artifact_namespace = fallback_artifact_namespace
        rows.append(
            ExecutionLane(
                lane_id=lane_id,
                execution_profile=spec.execution_profile,
                adapter=str(resolved.profile.adapter),
                replica_index=spec.replica_index,
                lane_label=spec.lane_label,
                artifact_namespace=artifact_namespace,
                comparison_role=spec.comparison_role,
                overrides=dict(spec.overrides),
            )
        )
    return rows


def _resolve_lane_profile(
    registry: ExecutionProfileRegistry,
    spec: LaneSpec,
) -> ResolvedExecutionProfile:
    try:
        return registry.resolve(spec.execution_profile)
    except ValueError as exc:
        raise ValueError(
            f"lane_matrix lane {spec.lane_id or spec.execution_profile!r}: {exc}"
        ) from exc


def expand_step_task_instances(
    step: ResolvedStep,
    lanes: Sequence[ExecutionLane],
) -> list[TaskInstance]:
    """Return concrete task instances for one resolved step.

    The Cartesian product is ``lanes × items``: for fan-out steps the items
    come from ``step.fan_out.items``; for non-fan-out steps a single
    sentinel item with ``item_key="_step"`` represents the step itself.
    Lanes without a resolved fan-out item key are still materialized so
    operators can preview a step that hasn't yet had its source scanned.
    """
    if not lanes:
        return []
    items = _step_items(step)
    instances: list[TaskInstance] = []
    for lane in lanes:
        for item_key, item_context in items:
            instances.append(
                TaskInstance(
                    task_id=derive_task_id(
                        step_id=step.step_id,
                        lane_id=lane.lane_id,
                        item_key=item_key,
                    ),
                    step_id=step.step_id,
                    lane_id=lane.lane_id,
                    item_key=item_key,
                    execution_profile=lane.execution_profile,
                    replica_index=lane.replica_index,
                    item_context=dict(item_context),
                )
            )
    return instances


def _step_items(step: ResolvedStep) -> list[tuple[str, dict[str, str]]]:
    """Return ``(item_key, item_context)`` pairs for a resolved step."""
    if step.fan_out is None:
        return [("_step", {})]
    bind = step.fan_out.bind
    pairs: list[tuple[str, dict[str, str]]] = []
    for item in step.fan_out.items:
        key = str(item.get(bind, "")).strip()
        if not key:
            # Skip items without a binding value — they cannot address a
            # task instance, but the planner has already warned upstream.
            continue
        pairs.append((key, dict(item)))
    return pairs


def count_task_instances(
    steps: Sequence[ResolvedStep],
    lanes: Sequence[ExecutionLane],
) -> dict[str, int]:
    """Return a per-step count of task instances for dry-run rendering."""
    return {step.step_id: len(expand_step_task_instances(step, lanes)) for step in steps}


def iter_all_task_instances(
    steps: Iterable[ResolvedStep],
    lanes: Sequence[ExecutionLane],
) -> Iterable[TaskInstance]:
    """Yield task instances across every step in *steps*."""
    for step in steps:
        yield from expand_step_task_instances(step, lanes)
