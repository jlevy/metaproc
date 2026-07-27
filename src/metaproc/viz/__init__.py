"""Pure projection from a resolved Plan to a VizModel.

This package only depends on ``metaproc.models`` and the standard library —
see ``metaproc/tests/test_viz_dep_containment.py``. Engine-side loaders that
need to read a ``.process.md`` file from disk live under ``metaproc.commands`` or
the browser module.
"""

from metaproc.viz.layout import layout_viz_model
from metaproc.viz.project import PlanBundle, build_viz_model
from metaproc.viz.render_html import render_html
from metaproc.viz.render_svg import parse_svg_metadata, render_svg

__all__ = [
    "PlanBundle",
    "build_viz_model",
    "layout_viz_model",
    "parse_svg_metadata",
    "render_html",
    "render_svg",
]
