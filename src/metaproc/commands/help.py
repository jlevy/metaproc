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
from metaproc.docs import TOPIC_DESCRIPTIONS as _TOPIC_DESCRIPTIONS
from metaproc.docs import load_help_topics


def _should_render(*, raw: bool, pager: bool, tty: bool) -> bool:
    """Decide rendered-markdown (True) vs raw stdout (False).

    ``--raw`` always wins; then ``--pager``; otherwise render only on a TTY.
    """
    if raw:
        return False
    if pager:
        return True
    return tty


def _topic_markdown(topic: str) -> str:
    return str(getattr(load_help_topics(), topic))


@app.command("help")
def help_topic(
    topic: str | None = typer.Argument(None, help=f"One of: {', '.join(_TOPIC_DESCRIPTIONS)}"),
    raw: bool = typer.Option(False, "--raw", help="Raw markdown to stdout, even on a TTY."),
    pager: bool = typer.Option(False, "--pager", help="Paged rendered output, even off a TTY."),
) -> None:
    """Print a bundled metaproc doc, or list topics when none is given."""
    if not topic:
        typer.echo("Topics (metaproc help <topic>):")
        for name, desc in _TOPIC_DESCRIPTIONS.items():
            typer.echo(f"  {name:<10} {desc}")
        raise typer.Exit(0)

    if topic not in _TOPIC_DESCRIPTIONS:
        typer.echo(
            f"Unknown topic: {topic!r}. Available: {', '.join(_TOPIC_DESCRIPTIONS)}.",
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
