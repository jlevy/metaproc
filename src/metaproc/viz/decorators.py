"""Merge plugin-contributed :class:`NodeDecoration` values into rendered nodes.

Plugins register ``(predicate, callback)`` pairs through
:meth:`metaproc.plugins.registry.PluginRegistryImpl.register_viz_decorator`.
The helpers here apply those pairs to a :class:`VizModel` at render time —
the source model is never mutated, keeping the projection contract intact.
"""

from __future__ import annotations

from dataclasses import dataclass

from metaproc.models.viz import NodeDecoration, VizModel, VizNode
from metaproc.plugins.protocol import VizDecorator


@dataclass(frozen=True)
class DecoratedNode:
    """A VizNode paired with merged :class:`NodeDecoration`.

    ``decoration`` is ``None`` when no registered decorator matched, letting
    renderers skip decoration rendering without a per-node branch.
    """

    node: VizNode
    decoration: NodeDecoration | None


def decorate_nodes(
    model: VizModel,
    decorators: list[VizDecorator],
) -> list[DecoratedNode]:
    """Return a :class:`DecoratedNode` for each node in ``model``."""
    results: list[DecoratedNode] = []
    for node in model.nodes:
        decoration = _merge_decorations(node, decorators)
        results.append(DecoratedNode(node=node, decoration=decoration))
    return results


def _merge_decorations(
    node: VizNode,
    decorators: list[VizDecorator],
) -> NodeDecoration | None:
    badge: str | None = None
    icon: str | None = None
    accent_token: str | None = None
    tooltip_parts: list[str] = []
    matched = False

    for decorator in decorators:
        if not decorator.predicate(node):
            continue
        matched = True
        dec = decorator.decorate(node)
        if dec.badge and badge is None:
            badge = dec.badge
        if dec.icon and icon is None:
            icon = dec.icon
        if dec.accent_token and accent_token is None:
            accent_token = dec.accent_token
        if dec.tooltip_addendum:
            tooltip_parts.append(dec.tooltip_addendum)

    if not matched:
        return None
    tooltip = "\n".join(tooltip_parts) if tooltip_parts else None
    return NodeDecoration(
        badge=badge,
        icon=icon,
        accent_token=accent_token,
        tooltip_addendum=tooltip,
    )


__all__ = ["DecoratedNode", "decorate_nodes"]
