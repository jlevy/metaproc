from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

import pytest

import metaproc.console as console_mod
from metaproc.console import console_pager, get_console, is_tty


def test_get_console_is_singleton() -> None:
    assert get_console() is get_console()


def test_is_tty_returns_bool() -> None:
    assert isinstance(is_tty(), bool)


def test_console_pager_passthrough_when_non_interactive() -> None:
    # Under pytest stdout is not interactive -> passthrough, yields cleanly.
    with console_pager(use_pager=True):
        pass


def test_console_pager_no_pager_when_disabled() -> None:
    with console_pager(use_pager=False):
        pass


def test_console_pager_enters_pager_when_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    entered = {"value": False}

    class _FakeConsole:
        is_interactive = True

        @contextmanager
        def pager(self, styles: bool = True) -> Generator[None]:
            entered["value"] = True
            yield

    monkeypatch.setattr(console_mod, "get_console", lambda: _FakeConsole())
    with console_pager(use_pager=True):
        pass
    assert entered["value"] is True
