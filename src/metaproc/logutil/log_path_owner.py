"""Derive ownership IDs (process / step / item) from a log file path.

Run directories already encode ownership in their layout. A pi-cli /
agent log lives at one of these paths, relative to the run dir:

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

from dataclasses import dataclass
from pathlib import Path

from metaproc.models.node_ids import (
    ROOT_SUBGRAPH_KEY,
    child_subgraph_key,
    process_node_id,
    step_node_id,
)
from metaproc.models.plan_bundle import PlanBundle

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


def _relative_to_run(log_path: Path, run_dir: Path) -> Path | None:
    candidate = log_path if log_path.is_absolute() else run_dir / log_path
    try:
        return candidate.resolve().relative_to(run_dir.resolve())
    except (ValueError, OSError):
        return None


def _structural_parts(relative: Path) -> list[str]:
    parts = list(relative.parts)
    # Trim everything from the trailing ``.logs`` segment onward so what
    # remains is just the structural ownership chain.
    if LOGS_DIRNAME in parts:
        return parts[: parts.index(LOGS_DIRNAME)]
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
            return _owner_from_bundle_parts(
                tail,
                bundle.children[step_id],
                subgraph_key=child_key,
                is_process_events_file=is_process_events_file,
            )
        if is_process_events_file:
            return LogOwner(
                process_node_id=process_node_id(child_key),
                step_node_id=None,
                item_key=None,
                subgraph_key=child_key,
            )

    return _owner_from_parts(parts, subgraph_key=subgraph_key)
