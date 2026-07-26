"""Wrap an SVG render in a minimal self-contained HTML document.

Suitable for copy-paste into emails, PR descriptions, or anywhere a full
browser tab is wanted but no network fetches are allowed.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from metaproc.models.viz import VizModel
from metaproc.plugins.protocol import VizDecorator
from metaproc.viz.render_svg import render_svg

_HTML_TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>{title}</title>
<style>
  /* Standalone palette — mirrors the --standalone-* tokens in the
     browser app's styles.css so the offline doc and live viz share
     naming. No literal colors below this block. */
  :root {{
    --sv-bg: hsl(0 0% 98%);
    --sv-surface: hsl(0 0% 100%);
    --sv-border: hsl(0 0% 87%);
    --sv-text: hsl(0 0% 7%);
    --sv-text-muted: hsl(0 0% 33%);
  }}
  body {{ margin:0; padding:24px; font-family:ui-sans-serif,system-ui,sans-serif;
          color:var(--sv-text); background:var(--sv-bg); }}
  header {{ margin-bottom:16px; }}
  header h1 {{ font-size:20px; margin:0 0 4px 0; }}
  header p {{ margin:0; color:var(--sv-text-muted); }}
  main {{ overflow:auto; background:var(--sv-surface); border:1px solid var(--sv-border);
          border-radius:8px; padding:16px; }}
</style>
</head>
<body>
<header>
<h1>{header_name}</h1>
<p>{header_description}</p>
</header>
<main>
{svg}
</main>
</body>
</html>
"""


def render_html(
    model: VizModel,
    *,
    decorators: list[VizDecorator] | None = None,
) -> str:
    """Render a :class:`VizModel` to a self-contained HTML page."""
    svg = render_svg(model, decorators=decorators)
    header = model.header
    return _HTML_TEMPLATE.format(
        title=escape(f"{header.name} — metaproc viz"),
        header_name=escape(header.name),
        header_description=escape(header.description or ""),
        svg=svg,
    )


__all__ = ["render_html"]
