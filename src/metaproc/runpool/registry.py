"""Backend registry for LaunchBackend discovery.

Backends are registered by name and resolved at runtime by the
``--backend`` CLI flag on ``run-parallel``.  ``LocalBackend`` is always
registered as ``"local"``.  External backends can also register
via the ``metaproc.backends`` entry-point group or by calling
``register_backend`` directly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from importlib.metadata import entry_points

from metaproc.runpool.backend import LaunchBackend, LocalBackend
from metaproc.runpool.mock_backend import MockBackend

log = logging.getLogger(__name__)

# Factory: callable that returns a LaunchBackend instance.
BackendFactory = Callable[[], LaunchBackend]

_registry: dict[str, BackendFactory] = {}
_entry_points_loaded = False


def register_backend(name: str, factory: BackendFactory) -> None:
    """Register a backend factory by name.

    Overwrites any existing registration for the same name.
    """
    _registry[name] = factory


def _load_entry_points() -> None:
    """Discover backends from the ``metaproc.backends`` entry-point group."""
    global _entry_points_loaded  # noqa: PLW0603
    if _entry_points_loaded:
        return
    _entry_points_loaded = True

    for ep in entry_points(group="metaproc.backends"):
        try:
            factory = ep.load()
            register_backend(ep.name, factory)
            log.debug("Loaded backend %r from entry point %s", ep.name, ep)
        except Exception:
            log.warning("Failed to load backend entry point %s", ep.name, exc_info=True)


def get_backend(name: str) -> LaunchBackend:
    """Resolve a backend by name.

    Raises ``KeyError`` with a helpful message listing available backends
    if the name is not found.
    """
    # Ensure built-in backends are always available.
    if "local" not in _registry:
        _registry["local"] = LocalBackend
    if "mock" not in _registry:
        _registry["mock"] = MockBackend

    _load_entry_points()

    factory = _registry.get(name)
    if factory is None:
        available = sorted(_registry.keys())
        msg = f"Unknown backend {name!r}. Available backends: {', '.join(available)}"
        raise KeyError(msg)
    return factory()


def available_backends() -> list[str]:
    """Return sorted list of registered backend names."""
    if "local" not in _registry:
        _registry["local"] = LocalBackend
    if "mock" not in _registry:
        _registry["mock"] = MockBackend
    _load_entry_points()
    return sorted(_registry.keys())


def reset_registry() -> None:
    """Clear all registrations (for testing)."""
    global _entry_points_loaded  # noqa: PLW0603
    _registry.clear()
    _entry_points_loaded = False
