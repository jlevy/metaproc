"""Tests for backend registry."""

from __future__ import annotations

import pytest

from metaproc.runpool.backend import LocalBackend
from metaproc.runpool.registry import (
    available_backends,
    get_backend,
    register_backend,
    reset_registry,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset registry before and after each test."""
    reset_registry()
    yield
    reset_registry()


class TestBackendRegistry:
    def test_local_always_available(self):
        backend = get_backend("local")
        assert isinstance(backend, LocalBackend)
        assert backend.name == "local"

    def test_unknown_backend_raises(self):
        with pytest.raises(KeyError, match="Unknown backend 'nonexistent'"):
            get_backend("nonexistent")

    def test_error_lists_available_backends(self):
        with pytest.raises(KeyError, match="local"):
            get_backend("nonexistent")

    def test_register_custom_backend(self):
        class StubBackend:
            @property
            def name(self) -> str:
                return "stub"

        register_backend("stub", StubBackend)  # pyright: ignore[reportArgumentType]
        backend = get_backend("stub")
        assert backend.name == "stub"

    def test_available_backends_includes_local(self):
        names = available_backends()
        assert "local" in names

    def test_available_backends_includes_registered(self):
        register_backend("custom", LocalBackend)
        names = available_backends()
        assert "custom" in names
        assert "local" in names

    def test_register_overwrites(self):
        class Backend1:
            @property
            def name(self) -> str:
                return "v1"

        class Backend2:
            @property
            def name(self) -> str:
                return "v2"

        register_backend("test", Backend1)  # pyright: ignore[reportArgumentType]
        register_backend("test", Backend2)  # pyright: ignore[reportArgumentType]
        backend = get_backend("test")
        assert backend.name == "v2"
