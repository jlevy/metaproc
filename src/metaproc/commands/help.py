"""``metaproc help <topic>`` — serve bundled docs, TTY-aware.

Non-TTY (pipes, agent stdout capture, file redirection): raw markdown to stdout,
no ANSI/pagination — one command, full doc, immediate agent context. Interactive
TTY: markdown rendered via ``rich.markdown.Markdown`` inside a pager. ``--raw``
forces raw even on a TTY; ``--pager`` forces paged rendered output even off one.
``metaproc help`` with no topic lists the topics and exits 0.
"""

from __future__ import annotations

import sys

import typer

from metaproc.cli import app
from metaproc.console import console_pager, get_console, is_tty
from metaproc.docs import TOPIC_BY_NAME as _TOPIC_BY_NAME
from metaproc.docs import TOPIC_REGISTRY as _TOPIC_REGISTRY
from metaproc.docs import topic_markdown as _topic_markdown


def _should_render(*, raw: bool, pager: bool, tty: bool) -> bool:
    """Decide rendered-markdown (True) vs raw stdout (False).

    ``--raw`` always wins; then ``--pager``; otherwise render only on a TTY.
    """
    if raw:
        return False
    if pager:
        return True
    return tty


def _approx_size(words: int) -> str:
    """Render an approximate topic size for the listing.

    Reading a topic costs the caller its whole document — roughly 30k tokens for
    the largest one. Agents in particular choose a topic before they can see what
    it costs, so the listing says up front.
    """
    return f"~{words / 1000:.1f}k words" if words >= 1000 else f"~{words} words"


@app.command("help")
def help_topic(
    topic: str | None = typer.Argument(None, help=f"One of: {', '.join(_TOPIC_BY_NAME)}"),
    raw: bool = typer.Option(False, "--raw", help="Raw markdown to stdout, even on a TTY."),
    pager: bool = typer.Option(False, "--pager", help="Paged rendered output, even off a TTY."),
) -> None:
    """Print a bundled metaproc doc, or list topics when none is given."""
    if not topic:
        typer.echo("Topics (metaproc help <topic>), in recommended reading order:")
        sizes = {entry.name: _approx_size(entry.words) for entry in _TOPIC_REGISTRY}
        name_width = max(len(name) for name in sizes)
        size_width = max(len(size) for size in sizes.values())
        for entry in _TOPIC_REGISTRY:
            size = sizes[entry.name]
            typer.echo(f"  {entry.name:<{name_width}}  {size:>{size_width}}  {entry.description}")
        raise typer.Exit(0)

    if topic not in _TOPIC_BY_NAME:
        typer.echo(
            f"Unknown topic: {topic!r}. Available: {', '.join(_TOPIC_BY_NAME)}.",
            err=True,
        )
        raise typer.Exit(2)

    markdown = _topic_markdown(topic)
    if _should_render(raw=raw, pager=pager, tty=is_tty()):
        from rich.markdown import (  # noqa: PLC0415 -- pre-existing local import; needs review
            Markdown,
        )

        with console_pager(use_pager=True):
            get_console().print(Markdown(markdown))
    else:
        sys.stdout.write(markdown)
