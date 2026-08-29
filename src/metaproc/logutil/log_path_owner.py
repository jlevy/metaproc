"""Derive ownership IDs (process / step / item) from a log file path.

Run directories already encode ownership in their layout. Current task logs live at:

    .logs/tasks/<step_id>/<item_key>/<file>.jsonl

Composite descendants prefix their own run directory. Historical layouts remain
readable and placed ``.logs`` after the structural chain:

    <step_id>/<item_key>/.logs/<file>.jsonl       (fan-out, no variant)
    <step_id>/<variant>/<item_key>/.logs/<file>.jsonl  (fan-out with variant)
    <step_id>/.logs/<file>.jsonl                  (scalar step)

Composite-descendant logs live under additional ``<composite>/`` prefix
segments. Callers with a `PlanBundle` should use `derive_owner_for_bundle`
so nested process paths resolve to qualified process and step node IDs.
The path-only helper is the cheap fallback for callers that cannot load a
plan and should preserve the full item path instead of guessing which
segment is a variant.

The helper is intentionally pure: it operates on paths only, never
touches the filesystem, and returns ``None`` for any segment it can't
derive — callers know to leave the corresponding `UsageBucket` field
``None`` rather than fabricate one.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from metaproc.models.node_ids import (
    ROOT_SUBGRAPH_KEY,
    child_subgraph_key,
    process_node_id,
    step_node_id,
)
from metaproc.models.plan_bundle import PlanBundle
from metaproc.models.resources import Node

LOGS_DIRNAME = ".logs"
PROCESS_EVENTS_FILENAME = "process-events.jsonl"


@dataclass(frozen=True)
class LogOwner:
    """Ownership IDs derived for a single log file."""

    process_node_id: str | None
    step_node_id: str | None
    item_key: str | None
    subgraph_key: str = ROOT_SUBGRAPH_KEY
    variant: str | None = None


_NULL_OWNER = LogOwner(
    process_node_id=None,
    step_node_id=None,
    item_key=None,
)


def derive_owner(log_path: Path, run_dir: Path) -> LogOwner:
    """Return the ownership triple for ``log_path`` under ``run_dir``.

    ``log_path`` may be absolute or relative; both are normalised before
    extraction. Paths outside ``run_dir`` yield an all-``None`` owner so
    callers can drop them without crashing.
    """
    relative = _relative_to_run(log_path, run_dir)
    if relative is None:
        return _NULL_OWNER

    parts = _structural_parts(relative)
    return _owner_from_parts(parts, subgraph_key=ROOT_SUBGRAPH_KEY)


def derive_owner_for_bundle(log_path: Path, run_dir: Path, bundle: PlanBundle) -> LogOwner:
    """Return the deepest plan-aware ownership assignment for ``log_path``.

    Composite child processes write under ``<parent-run-dir>/<step-id>/``.
    Resolving with the bundle lets the rollup map
    ``parent_step/child_step/item/.logs/session.jsonl`` to the child step's
    qualified node ID instead of treating ``parent_step`` as the owning step.
    """
    relative = _relative_to_run(log_path, run_dir)
    if relative is None:
        return _NULL_OWNER
    parts = _structural_parts(relative)
    return _owner_from_bundle_parts(
        parts,
        bundle,
        subgraph_key=ROOT_SUBGRAPH_KEY,
        is_process_events_file=relative.name == PROCESS_EVENTS_FILENAME,
    )


def derive_owner_for_hierarchy(
    log_path: Path,
    run_dir: Path,
    hierarchy: Node,
    *,
    mapped_composite_step_ids: Collection[str] | None = None,
) -> LogOwner:
    """Resolve ownership from the immutable hierarchy when source specs are unavailable."""
    relative = _relative_to_run(log_path, run_dir)
    if relative is None:
        return _NULL_OWNER
    parts = _structural_parts(relative)
    process = _root_process_node(hierarchy)
    if process is None:
        return _NULL_OWNER
    return _owner_from_hierarchy_parts(
        parts,
        process,
        is_process_events_file=relative.name == PROCESS_EVENTS_FILENAME,
        mapped_composite_step_ids=(
            frozenset(mapped_composite_step_ids) if mapped_composite_step_ids is not None else None
        ),
    )


def _relative_to_run(log_path: Path, run_dir: Path) -> Path | None:
    try:
        resolved_run = run_dir.resolve()
    except OSError:
        return None

    candidates = [log_path] if log_path.is_absolute() else [log_path, run_dir / log_path]
    for candidate in candidates:
        try:
            return candidate.resolve().relative_to(resolved_run)
        except (ValueError, OSError):
            continue
    return None


def _structural_parts(relative: Path) -> list[str]:
    parts = list(relative.parts)
    # Modern task logs are ``<composite-prefix>/.logs/tasks/<step>/<item>/<file>``.
    # Older runs placed ``.logs`` after the step/item chain. Normalize both to
    # one structural sequence consumed by the plan/hierarchy walkers.
    if LOGS_DIRNAME in parts:
        index = parts.index(LOGS_DIRNAME)
        prefix = parts[:index]
        suffix = parts[index + 1 :]
        if suffix and suffix[0] == "tasks":
            return [*prefix, *suffix[1:-1]]
        return prefix
    # No `.logs` segment: drop only the file name.
    if parts:
        parts.pop()
    return parts


def _owner_from_parts(parts: list[str], *, subgraph_key: str) -> LogOwner:
    if not parts:
        return LogOwner(
            process_node_id=process_node_id(subgraph_key),
            step_node_id=None,
            item_key=None,
            subgraph_key=subgraph_key,
        )

    step_id = parts[0]
    tail = parts[1:]
    variant: str | None = None
    item_key: str | None = None
    if len(tail) >= 2:
        variant = tail[0]
        item_key = "/".join(tail)
    elif len(tail) == 1:
        item_key = tail[0]

    return LogOwner(
        process_node_id=process_node_id(subgraph_key),
        step_node_id=step_node_id(subgraph_key, step_id),
        item_key=item_key,
        subgraph_key=subgraph_key,
        variant=variant,
    )


def _owner_from_bundle_parts(
    parts: list[str],
    bundle: PlanBundle,
    *,
    subgraph_key: str,
    is_process_events_file: bool,
) -> LogOwner:
    if not parts:
        return _owner_from_parts(parts, subgraph_key=subgraph_key)

    step_id = parts[0]
    tail = parts[1:]
    step = next((s for s in bundle.plan.steps if s.step_id == step_id), None)
    if step is None:
        return _owner_from_parts(parts, subgraph_key=subgraph_key)

    if step.mode == "composite" and step_id in bundle.children:
        child_key = child_subgraph_key(subgraph_key, step_id)
        if tail:
            child_parts = tail[1:] if step.fan_out is not None else tail
            owner = _owner_from_bundle_parts(
                child_parts,
                bundle.children[step_id],
                subgraph_key=child_key,
                is_process_events_file=is_process_events_file,
            )
            return _prefix_item_key(owner, tail[0]) if step.fan_out is not None else owner
        if is_process_events_file:
            return LogOwner(
                process_node_id=process_node_id(child_key),
                step_node_id=None,
                item_key=None,
                subgraph_key=child_key,
            )

    return _owner_from_parts(parts, subgraph_key=subgraph_key)


def _root_process_node(hierarchy: Node) -> Node | None:
    if hierarchy.node_type == "process":
        return hierarchy
    return next((child for child in hierarchy.children if child.node_type == "process"), None)


def _owner_from_hierarchy_parts(
    parts: list[str],
    process: Node,
    *,
    is_process_events_file: bool,
    mapped_composite_step_ids: frozenset[str] | None,
) -> LogOwner:
    subgraph_key = process.node_id.removeprefix("process:")
    if not parts:
        return LogOwner(
            process_node_id=process.node_id,
            step_node_id=None,
            item_key=None,
            subgraph_key=subgraph_key,
        )

    step = next(
        (
            child
            for child in process.children
            if child.node_type == "step" and (child.label == parts[0] or child.node_id == parts[0])
        ),
        None,
    )
    if step is None:
        return LogOwner(
            process_node_id=process.node_id,
            step_node_id=None,
            item_key=None,
            subgraph_key=subgraph_key,
        )

    nested = next((child for child in step.children if child.node_type == "process"), None)
    tail = parts[1:]
    if nested is not None and (tail or is_process_events_file):
        child_step_ids = {
            value
            for child in nested.children
            if child.node_type == "step"
            for value in (child.label, child.node_id)
        }
        mapped_item_key: str | None = None
        child_parts = tail
        if mapped_composite_step_ids is not None and step.node_id in mapped_composite_step_ids:
            if tail:
                mapped_item_key = tail[0]
                child_parts = tail[1:]
        elif (
            mapped_composite_step_ids is None
            and len(tail) >= 2
            and tail[0] not in child_step_ids
            and tail[1] in child_step_ids
        ):
            mapped_item_key = tail[0]
            child_parts = tail[1:]
        owner = _owner_from_hierarchy_parts(
            child_parts,
            nested,
            is_process_events_file=is_process_events_file,
            mapped_composite_step_ids=mapped_composite_step_ids,
        )
        return _prefix_item_key(owner, mapped_item_key) if mapped_item_key is not None else owner

    item_key = "/".join(tail) if tail else None
    variant = tail[0] if len(tail) >= 2 else None
    return LogOwner(
        process_node_id=process.node_id,
        step_node_id=step.node_id,
        item_key=item_key,
        subgraph_key=subgraph_key,
        variant=variant,
    )


def _prefix_item_key(owner: LogOwner, prefix: str) -> LogOwner:
    item_key = f"{prefix}/{owner.item_key}" if owner.item_key else prefix
    return LogOwner(
        process_node_id=owner.process_node_id,
        step_node_id=owner.step_node_id,
        item_key=item_key,
        subgraph_key=owner.subgraph_key,
        variant=owner.variant,
    )
