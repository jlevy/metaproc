"""Deterministic layered layout for :class:`VizModel`.

Given a :class:`VizModel` with pre-computed ``levels`` from the projection,
produce a :class:`LaidOut` with absolute xy coordinates and orthogonal edge
waypoints. The layout is pure python and produces byte-stable output so static
SVGs in docs/PRs survive round-tripping.

Aesthetics follow a Sugiyama sketch (layered top-down with barycentric
within-layer ordering). If a more advanced layout is needed later, a
``grandalf`` or ``graphviz`` adapter can replace :func:`layout_viz_model`
wholesale — the output schema stays the same.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from metaproc.models.viz import LaidOut, LaidOutEdge, LaidOutNode, VizModel

NODE_WIDTH = 220.0
NODE_HEIGHT = 72.0
H_GAP = 72.0
V_GAP = 56.0
PADDING = 40.0


@dataclass(frozen=True)
class _LevelLayout:
    level_index: int
    ordered_ids: list[str]


def layout_viz_model(model: VizModel) -> LaidOut:
    """Layer + place every node, then route edges orthogonally."""

    levels = _assign_levels(model)
    ordered_levels = [_barycentric_order(level, levels, model) for level in levels]
    positions = _position_nodes(ordered_levels)
    laid_nodes = [
        LaidOutNode(
            id=node_id,
            x=xy[0],
            y=xy[1],
            width=NODE_WIDTH,
            height=NODE_HEIGHT,
        )
        for node_id, xy in positions.items()
    ]
    laid_edges = [
        _route_edge(edge.source, edge.target, positions)
        for edge in model.edges
        if edge.source in positions and edge.target in positions
    ]
    width = max((n.x + n.width for n in laid_nodes), default=0.0) + PADDING
    height = max((n.y + n.height for n in laid_nodes), default=0.0) + PADDING
    return LaidOut(
        model=model,
        nodes=laid_nodes,
        edges=laid_edges,
        width=width,
        height=height,
    )


def _assign_levels(model: VizModel) -> list[_LevelLayout]:
    """Use ``VizModel.levels`` when present; otherwise fall back to BFS.

    ``model.levels`` covers the step topology. Any node not mentioned there
    (deps, child subgraph nodes) is appended on a trailing level so every node
    ends up placed.
    """
    known_ids = {n.id for n in model.nodes}
    levels: list[_LevelLayout] = []
    if model.levels:
        for i, level in enumerate(model.levels):
            levels.append(
                _LevelLayout(level_index=i, ordered_ids=[nid for nid in level if nid in known_ids])
            )
    placed = {nid for lvl in levels for nid in lvl.ordered_ids}
    leftover = [n.id for n in model.nodes if n.id not in placed]
    if leftover:
        levels.append(_LevelLayout(level_index=len(levels), ordered_ids=leftover))
    if not levels:
        levels.append(_LevelLayout(level_index=0, ordered_ids=[n.id for n in model.nodes]))
    return levels


def _barycentric_order(
    level: _LevelLayout,
    all_levels: list[_LevelLayout],
    model: VizModel,
) -> _LevelLayout:
    """Reorder a level by the mean position of its in-neighbors on the prior level."""
    if level.level_index == 0:
        return level
    prior_ids = all_levels[level.level_index - 1].ordered_ids
    prior_index = {nid: i for i, nid in enumerate(prior_ids)}

    predecessors: dict[str, list[int]] = defaultdict(list)
    for edge in model.edges:
        if edge.target in level.ordered_ids and edge.source in prior_index:
            predecessors[edge.target].append(prior_index[edge.source])

    def key(node_id: str) -> tuple[float, int]:
        preds = predecessors.get(node_id, [])
        barycenter = sum(preds) / len(preds) if preds else float(len(prior_ids))
        original_index = level.ordered_ids.index(node_id)
        return (barycenter, original_index)

    return _LevelLayout(
        level_index=level.level_index,
        ordered_ids=sorted(level.ordered_ids, key=key),
    )


def _position_nodes(levels: list[_LevelLayout]) -> dict[str, tuple[float, float]]:
    positions: dict[str, tuple[float, float]] = {}
    row_stride = NODE_HEIGHT + V_GAP
    col_stride = NODE_WIDTH + H_GAP
    for level in levels:
        row = level.level_index
        # Horizontally center each row around a common axis.
        count = len(level.ordered_ids)
        row_width = count * col_stride - H_GAP if count else 0
        x_start = PADDING + max(0.0, -row_width / 2.0)
        y = PADDING + row * row_stride
        for col, node_id in enumerate(level.ordered_ids):
            x = x_start + col * col_stride
            positions[node_id] = (x, y)
    # Shift so the leftmost node sits at PADDING.
    if positions:
        min_x = min(x for x, _ in positions.values())
        if min_x < PADDING:
            shift = PADDING - min_x
            positions = {k: (x + shift, y) for k, (x, y) in positions.items()}
    return positions


def _route_edge(
    source_id: str,
    target_id: str,
    positions: dict[str, tuple[float, float]],
) -> LaidOutEdge:
    sx, sy = positions[source_id]
    tx, ty = positions[target_id]
    # Orthogonal 3-segment: start-bottom → midline → target-top.
    start = (sx + NODE_WIDTH / 2.0, sy + NODE_HEIGHT)
    end = (tx + NODE_WIDTH / 2.0, ty)
    mid_y = (start[1] + end[1]) / 2.0
    waypoints: list[tuple[float, float]] = [
        start,
        (start[0], mid_y),
        (end[0], mid_y),
        end,
    ]
    return LaidOutEdge(source=source_id, target=target_id, waypoints=waypoints)
