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
        if dep is not None and _requires_only_finished(dep, failed_step_id, steps):
            # The consumer asked for terminal outcomes, not successful ones, so a
            # partially failed upstream satisfies its edge. Blocking it here would let
            # an operator-facing failure decide an edge's meaning, which is the one
            # thing edge semantics must not depend on.
            continue
        blocked.append(dep_id)
    return blocked


def _requires_only_finished(
    step: ResolvedStep, failed_step_id: str, steps: Sequence[ResolvedStep]
) -> bool:
    """Whether *step* tolerates *failed_step_id* failing, per a collected edge.

    The consumer declares `require: finished` against the step it collects, but the
    failure can land anywhere upstream of that step: an item dying at stage two is why
    stage three has partial coverage, and the consumer asked for exactly that. So the
    edge tolerates a failure at the collected step itself or anywhere feeding it.

    It does not tolerate failures outside that subtree. Those reach the consumer through
    a different edge, which said nothing about accepting terminal outcomes.
    """
    for spec in step.inputs.values():
        if spec.require != "finished" or not spec.collect:
            continue
        if spec.collect == failed_step_id:
            return True
        if spec.collect in downstream(steps, failed_step_id):
            return True
    return False


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


def item_aligned_chains(steps: Sequence[ResolvedStep]) -> list[list[str]]:
    """Return maximal chains of steps whose edges are item-scoped.

    A pair ``(a, b)`` is item-aligned when *b* declares ``for_each.align:
    same_key``, *b* needs *a* and nothing else, and both fan out over the same
    resolved source. Alignment is inferable only where identity is provable, so
    a shared source is required: matching key strings across unrelated rosters
    is coincidence, not identity, and aligning on it would silently join
    unrelated work.

    A step needing anything outside the chain does not extend it. That edge is
    genuinely step-scoped, and the level walk still has to honor it, so the
    chain ends rather than absorbing a barrier it cannot express.

    Chains are maximal and disjoint: every step appears in at most one, and a
    returned chain always has at least two steps, since a single step has no
    edge to align.
    """
    by_id = {s.step_id: s for s in steps}

    def _aligned_to(step: ResolvedStep) -> str | None:
        """Return the predecessor this step is item-aligned with, if any."""
        fan_out = step.fan_out
        if fan_out is None or fan_out.align != "same_key":
            return None
        if len(step.needs) != 1:
            # Two upstreams mean at least one edge this alignment does not
            # describe; treat the whole step as step-scoped rather than guess.
            return None
        upstream = by_id.get(step.needs[0])
        if upstream is None or upstream.fan_out is None:
            return None
        if upstream.fan_out.source != fan_out.source:
            return None
        return upstream.step_id

    # Collect every claimed edge first, so a fork is visible before any chain is
    # built. Resolving it by first-wins would make the result depend on step order
    # in the spec, which is not something an author states or can see.
    claims: dict[str, list[str]] = defaultdict(list)
    for step in steps:
        up = _aligned_to(step)
        if up is not None:
            claims[up].append(step.step_id)

    successor: dict[str, str] = {}
    predecessor: dict[str, str] = {}
    for up, claimants in claims.items():
        if len(claimants) != 1:
            # Two steps align to one upstream. Item-scoped forks are meaningful,
            # but a linear chain cannot express one, so both edges stay
            # step-scoped rather than silently keeping one branch.
            continue
        successor[up] = claimants[0]
        predecessor[claimants[0]] = up

    chains: list[list[str]] = []
    for step in steps:
        head = step.step_id
        if head in predecessor or head not in successor:
            continue
        chain = [head]
        while chain[-1] in successor:
            chain.append(successor[chain[-1]])
        chains.append(chain)
    return chains
