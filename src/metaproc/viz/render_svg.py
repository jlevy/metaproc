"""Render :class:`LaidOut` to a self-contained SVG document.

The output embeds the source :class:`VizModel` JSON inside an
``<svg><metadata>`` block so readers can round-trip a static SVG back into a
structural model — this preserves drill-down links when the file is copied
into a doc or attached to a PR.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from metaproc.models.viz import LaidOut, NodeDecoration, VizModel
from metaproc.plugins.protocol import VizDecorator
from metaproc.viz.decorators import decorate_nodes
from metaproc.viz.layout import NODE_HEIGHT, NODE_WIDTH, layout_viz_model

_SVG_NS = "http://www.w3.org/2000/svg"
_METAPROC_NS = "https://metaproc.dev/viz"


def render_svg(
    model: VizModel,
    *,
    decorators: list[VizDecorator] | None = None,
) -> str:
    """Project a :class:`VizModel` to a standalone SVG string.

    When ``decorators`` is provided, each plugin-contributed
    :class:`NodeDecoration` is merged into its matching rendered node.
    """
    laid = layout_viz_model(model)
    decorations = _build_decoration_map(model, decorators or [])
    return _render_laid_out(laid, decorations)


def parse_svg_metadata(svg_text: str) -> VizModel:
    """Inverse of :func:`render_svg` — extract the embedded :class:`VizModel`."""
    open_tag = '<metaproc:vizmodel xmlns:metaproc="' + _METAPROC_NS + '">'
    close_tag = "</metaproc:vizmodel>"
    start = svg_text.find(open_tag)
    if start < 0:
        msg = "SVG does not contain an embedded VizModel metadata block"
        raise ValueError(msg)
    start += len(open_tag)
    end = svg_text.find(close_tag, start)
    if end < 0:
        msg = "SVG metadata block is not closed"
        raise ValueError(msg)
    payload = svg_text[start:end]
    return VizModel.model_validate_json(_unescape_cdata(payload))


def _build_decoration_map(
    model: VizModel,
    decorators: list[VizDecorator],
) -> dict[str, NodeDecoration]:
    if not decorators:
        return {}
    return {
        dn.node.id: dn.decoration
        for dn in decorate_nodes(model, decorators)
        if dn.decoration is not None
    }


def _render_laid_out(laid: LaidOut, decorations: dict[str, NodeDecoration]) -> str:
    body_parts: list[str] = []
    body_parts.append(_metadata_block(laid.model))
    body_parts.append(_style_block())
    body_parts.extend(_render_edges(laid))
    body_parts.extend(_render_nodes(laid, decorations))

    width = max(int(laid.width), 1)
    height = max(int(laid.height), 1)
    return (
        f'<svg xmlns="{_SVG_NS}" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">' + "".join(body_parts) + "</svg>"
    )


def _metadata_block(model: VizModel) -> str:
    payload = model.model_dump_json(by_alias=True, exclude_none=True)
    return (
        "<metadata>"
        f'<metaproc:vizmodel xmlns:metaproc="{_METAPROC_NS}">'
        + _escape_cdata(payload)
        + "</metaproc:vizmodel>"
        "</metadata>"
    )


def _style_block() -> str:
    # Standalone SVG carries its own palette. Token names mirror the
    # --standalone-* tokens in browser/static/styles.css; literal HSL
    # values are declared only in :root.  :root inside <svg> matches
    # the outermost <svg> element, so custom properties inherit.
    return (
        "<defs><style>"
        ":root{"
        "--sv-node-fill:hsl(0 0% 97%);"
        "--sv-node-stroke:hsl(0 0% 20%);"
        "--sv-node-step-fill:hsl(210 100% 95%);"
        "--sv-node-dep-fill:hsl(40 100% 93%);"
        "--sv-node-process-fill:hsl(260 50% 96%);"
        "--sv-label:hsl(0 0% 7%);"
        "--sv-label-kind:hsl(0 0% 40%);"
        "--sv-label-badge:hsl(0 0% 27%);"
        "--sv-edge:hsl(0 0% 33%);"
        "}"
        ".viz-node{stroke:var(--sv-node-stroke);fill:var(--sv-node-fill);rx:6;ry:6;}"
        ".viz-node.step{fill:var(--sv-node-step-fill);}"
        ".viz-node.dep{fill:var(--sv-node-dep-fill);}"
        ".viz-node.process{fill:var(--sv-node-process-fill);stroke-dasharray:4 3;}"
        ".viz-label{font-family:ui-sans-serif,system-ui,sans-serif;font-size:13px;fill:var(--sv-label);}"
        ".viz-label.kind{font-size:10px;fill:var(--sv-label-kind);}"
        ".viz-label.badge{font-size:10px;fill:var(--sv-label-badge);font-weight:600;}"
        ".viz-edge{stroke:var(--sv-edge);stroke-width:1.2;fill:none;}"
        "</style></defs>"
    )


def _render_nodes(laid: LaidOut, decorations: dict[str, NodeDecoration]) -> list[str]:
    model_by_id = {n.id: n for n in laid.model.nodes}
    out: list[str] = []
    for placed in laid.nodes:
        viz_node = model_by_id.get(placed.id)
        if viz_node is None:
            continue
        kind = viz_node.kind
        label = escape(viz_node.label)
        href = _drill_down_href(viz_node)
        decoration = decorations.get(placed.id)
        accent_class = (
            f" accent-{decoration.accent_token}" if decoration and decoration.accent_token else ""
        )
        group_open = f'<g class="viz-node-group{accent_class}" data-id="{escape(placed.id)}">'
        if href:
            group_open = f'<a href="{escape(href)}">' + group_open
        rect = (
            f'<rect class="viz-node {kind}" '
            f'x="{placed.x:.1f}" y="{placed.y:.1f}" '
            f'width="{placed.width:.1f}" height="{placed.height:.1f}"/>'
        )
        kind_label = (
            f'<text class="viz-label kind" x="{placed.x + 10:.1f}" '
            f'y="{placed.y + 18:.1f}">{escape(kind.upper())}</text>'
        )
        name_label = (
            f'<text class="viz-label" x="{placed.x + 10:.1f}" '
            f'y="{placed.y + 40:.1f}">{label}</text>'
        )
        badge_elem = _render_badge(placed, decoration)
        group_close = "</g>" + ("</a>" if href else "")
        out.append(group_open + rect + kind_label + name_label + badge_elem + group_close)
    return out


def _render_badge(
    placed: object,
    decoration: NodeDecoration | None,
) -> str:
    if decoration is None or not decoration.badge:
        return ""
    x = getattr(placed, "x", 0.0)
    y = getattr(placed, "y", 0.0)
    width = getattr(placed, "width", 0.0)
    return (
        f'<text class="viz-label badge" x="{x + width - 10:.1f}" '
        f'y="{y + 18:.1f}" text-anchor="end">'
        f"{escape(decoration.badge)}</text>"
    )


def _render_edges(laid: LaidOut) -> list[str]:
    kind_by_pair = {(e.source, e.target): e.kind for e in laid.model.edges}
    out: list[str] = []
    for edge in laid.edges:
        pts = edge.waypoints
        if not pts:
            continue
        kind = kind_by_pair.get((edge.source, edge.target), "needs")
        path = f"M {pts[0][0]:.1f} {pts[0][1]:.1f}"
        for x, y in pts[1:]:
            path += f" L {x:.1f} {y:.1f}"
        out.append(f'<path class="viz-edge {kind}" d="{path}"/>')
    return out


def _drill_down_href(viz_node: object) -> str | None:
    """Return an on-disk relative path for path-bearing nodes, else None."""
    path = getattr(viz_node, "path", None)
    if isinstance(path, str) and path and "{{" not in path:
        return path
    dep = getattr(viz_node, "dep", None)
    if dep is not None:
        dep_path = getattr(dep, "path", None)
        if isinstance(dep_path, str) and dep_path and "{{" not in dep_path:
            return dep_path
    return None


def _escape_cdata(text: str) -> str:
    return text.replace("]]>", "]]]]><![CDATA[>")


def _unescape_cdata(text: str) -> str:
    return text.replace("]]]]><![CDATA[>", "]]>")


__all__ = ["render_svg", "parse_svg_metadata", "NODE_HEIGHT", "NODE_WIDTH"]
