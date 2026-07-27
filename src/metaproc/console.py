"""Generic rich console + pager helpers.

Depends only on ``rich``; no metaproc-internal imports. A process-wide
stdout-bound :class:`rich.console.Console` singleton plus a pager
contextmanager for long rendered output. Ported from kash's ``console_pager()``.

Used by ``metaproc help <topic>`` to page rendered markdown on an interactive
TTY while staying passthrough for pipes / captured output.
"""

from __future__ import annotations

import sys
from collections.abc import Generator
from contextlib import contextmanager
from functools import cache

from rich.console import Console


@cache
def get_console() -> Console:
    """Return the process-wide stdout-bound rich Console (default theme)."""
    return Console()


def is_tty() -> bool:
    """True when stdout is an interactive terminal."""
    console = get_console()
    if console.is_terminal:
        return True
    return sys.stdout.isatty()


@contextmanager
def console_pager(use_pager: bool = True) -> Generator[None]:
    """Page rendered output through the console's pager when interactive.

    Yields under ``console.pager(styles=True)`` when the console is interactive
    and ``use_pager`` is set; otherwise yields as a passthrough so piped or
    captured output is never paged.
    """
    console = get_console()
    if use_pager and console.is_interactive:
        with console.pager(styles=True):
            yield
    else:
        yield
