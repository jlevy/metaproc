"""Graph utilities for the step dependency (needs) graph.

Pure functions — no IO, no side effects. Operates on resolved steps
after variable resolution and fan-out discovery.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Sequence

from metaproc.models.plan import ResolvedStep


def validate_step_graph(steps: Sequence[ResolvedStep]) -> list[str]:
    """Return graph validation errors (duplicate IDs, dangling refs, cycles)."""
    errors: list[str] = []

    # Duplicate IDs
    seen: dict[str, int] = {}
    for step in steps:
        seen[step.step_id] = seen.get(step.step_id, 0) + 1
    for step_id, count in seen.items():
        if count > 1:
            errors.append(f"duplicate step id: {step_id!r} appears {count} times")

    # Dangling references
    valid_ids = {step.step_id for step in steps}
    for step in steps:
        for dep in step.needs:
            if dep not in valid_ids:
                errors.append(f"step {step.step_id!r} needs {dep!r}, which does not exist")

    # Cycles
    cycles = detect_cycles(steps)
    for cycle in cycles:
        errors.append(f"cycle detected: {' -> '.join(cycle)}")

    return errors


def detect_cycles(steps: Sequence[ResolvedStep]) -> list[list[str]]:
    """Return cycles found in the needs graph (empty = acyclic)."""
    adj: dict[str, list[str]] = defaultdict(list)
    all_ids: set[str] = set()
    for step in steps:
        all_ids.add(step.step_id)
        for dep in step.needs:
            # Edge from dep -> step (dep must run before step)
            adj[dep].append(step.step_id)

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {sid: WHITE for sid in all_ids}
    parent: dict[str, str | None] = {sid: None for sid in all_ids}
    cycles: list[list[str]] = []

    def dfs(u: str) -> None:
        color[u] = GRAY
        for v in adj[u]:
            if v not in color:
                continue
            if color[v] == GRAY:
                # Back edge — extract cycle
                cycle = [v, u]
                p = parent[u]
                while p is not None and p != v:
                    cycle.append(p)
                    p = parent[p]
                cycle.reverse()
                cycles.append(cycle)
            elif color[v] == WHITE:
                parent[v] = u
                dfs(v)
        color[u] = BLACK

    for sid in sorted(all_ids):
        if color[sid] == WHITE:
            dfs(sid)

    return cycles


def downstream(steps: Sequence[ResolvedStep], root_id: str) -> list[str]:
    """Return step IDs that transitively depend on root_id (topologically sorted).

    Does not include root_id itself.
    """
    # Build adjacency: dep -> list of dependents
    adj: dict[str, list[str]] = defaultdict(list)
    for step in steps:
        for dep in step.needs:
            adj[dep].append(step.step_id)

    # BFS to find all transitive dependents
    visited: set[str] = set()
    queue: deque[str] = deque([root_id])
    order: list[str] = []

    while queue:
        node = queue.popleft()
        for child in adj[node]:
            if child not in visited:
                visited.add(child)
                order.append(child)
                queue.append(child)

    return order


def propagate_failure(
    steps: Sequence[ResolvedStep],
    failed_step_id: str,
) -> list[str]:
    """Return the dependents of ``failed_step_id`` that should be blocked.

    Steps with ``on_failure == "continue"`` are excluded — they run
    regardless of upstream failure. This is the rollup / post-mortem
    escape hatch (e.g. ``run-stats`` always emitting even on partial
    cohort failure). All other transitive dependents are included.
    """
    by_id: dict[str, ResolvedStep] = {step.step_id: step for step in steps}
    blocked: list[str] = []
    for dep_id in downstream(steps, failed_step_id):
        dep = by_id.get(dep_id)
        if dep is not None and dep.on_failure == "continue":
            continue
        blocked.append(dep_id)
    return blocked


def topo_sort(
    steps: Sequence[ResolvedStep],
    step_ids: set[str] | None = None,
) -> list[list[str]]:
    """Topologically sort steps by needs, grouped into levels.

    Steps in the same level have no inter-dependencies and can run concurrently.
    If step_ids is provided, only those steps are included in the sort.
    Dependencies outside step_ids are treated as already satisfied.
    """
    if step_ids is None:
        step_ids = {step.step_id for step in steps}

    # Build needs map restricted to step_ids
    needs_map: dict[str, set[str]] = {}
    for step in steps:
        if step.step_id in step_ids:
            needs_map[step.step_id] = {dep for dep in step.needs if dep in step_ids}

    remaining = set(needs_map.keys())
    levels: list[list[str]] = []

    while remaining:
        # Find steps whose needs are all satisfied (not in remaining)
        level = sorted(sid for sid in remaining if not needs_map[sid] & remaining)
        if not level:
            # All remaining steps have unsatisfied deps — cycle (should
            # have been caught by validate_step_graph, but be safe).
            break
        levels.append(level)
        remaining -= set(level)

    return levels
