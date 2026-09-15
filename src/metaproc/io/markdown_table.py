"""Render Markdown pipe tables from cell values.

Callers pass cell text; this module owns the pipe syntax, the separator row, and cell
escaping, so no caller assembles table syntax inline.
"""

from __future__ import annotations

from collections.abc import Sequence

_ALIGNMENTS = frozenset({"left", "right"})


def render_markdown_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    align: Sequence[str] | None = None,
) -> str:
    """Return a pipe table with one line per row and no trailing newline.

    Each row must have one cell per header, and *align* holds ``left`` or ``right``
    per column. A ``|`` in a cell is escaped, and line breaks collapse to spaces, so a
    cell can never split the table.
    """
    alignment = list(align) if align is not None else ["left"] * len(headers)
    if len(alignment) != len(headers):
        raise ValueError("align must have one entry per header")
    if unknown := set(alignment) - _ALIGNMENTS:
        raise ValueError(f"unknown alignment: {sorted(unknown)}")
    lines = [
        _row(headers),
        _row(["---:" if side == "right" else "---" for side in alignment], escape=False),
    ]
    for row in rows:
        if len(row) != len(headers):
            raise ValueError(f"row has {len(row)} cells for {len(headers)} headers")
        lines.append(_row(row))
    return "\n".join(lines)


def _row(cells: Sequence[str], *, escape: bool = True) -> str:
    rendered = [_escape(cell) if escape else cell for cell in cells]
    return "| " + " | ".join(rendered) + " |"


def _escape(cell: str) -> str:
    return " ".join(cell.replace("|", "\\|").splitlines()) if cell else ""
