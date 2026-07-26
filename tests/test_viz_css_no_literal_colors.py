"""Enforce the Phase 0 lint rule for viz.css.

The visualization stylesheet must only express colors through ``var(--...)``
tokens; no hex / rgb / rgba / hsl / hsla / named-color literals. The tokens
themselves live in the MetaBrowser host and consumer plugin styles so that a theme
change in one place repaints the graph.

A violation of this test means a literal color slipped into viz.css —
either add the literal to the shared token palette and reference it via
``var(--viz-...)``, or reuse an existing token.
"""

from __future__ import annotations

import re

from metaproc.metabrowser_plugin import plugin_dir

VIZ_CSS = plugin_dir() / "viz.css"

_COLOR_LITERAL = re.compile(
    r"""
    (?:\#[0-9a-fA-F]{3,8})      # #rgb / #rrggbb / #rrggbbaa
  | (?:rgba?\s*\()              # rgb(...) rgba(...)
  | (?:hsla?\s*\()              # hsl(...) hsla(...)
    """,
    re.VERBOSE,
)


def test_viz_css_contains_no_literal_colors() -> None:
    text = VIZ_CSS.read_text()
    # Strip comments so example hex values inside /* ... */ don't trip the check.
    stripped = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    offenders: list[tuple[int, str]] = []
    for idx, line in enumerate(stripped.splitlines(), start=1):
        if _COLOR_LITERAL.search(line):
            offenders.append((idx, line.strip()))
    assert not offenders, (
        "viz.css must not contain literal colors — reference tokens via var(--viz-*) "
        "from the host or consumer plugin styles instead. Offenders:\n"
        + "\n".join(f"  line {lineno}: {line}" for lineno, line in offenders)
    )
