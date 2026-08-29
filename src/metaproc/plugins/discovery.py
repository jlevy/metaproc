"""Plugin discovery via installed entry points."""

from __future__ import annotations

import importlib.metadata
import logging
import os
from collections.abc import Mapping

from metaproc.plugins.registry import PluginRegistryImpl

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "metaproc.plugins"

# Global singleton set once by discover_and_load_plugins, accessible to commands.
_global_registry = PluginRegistryImpl()

# Whether an entry-point scan has already populated the global registry in this process.
_discovery_completed = False


def get_plugin_registry() -> PluginRegistryImpl:
    """Return the global plugin registry (populated at CLI startup)."""
    return _global_registry


def get_bootstrap_env_vars(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Resolve consumer-declared bootstrap env vars for cloud propagation."""
    source = os.environ if env is None else env
    resolved: dict[str, str] = {}
    for name, default in _global_registry.bootstrap_env_defaults.items():
        value = source.get(name, default)
        if value:
            resolved[name] = value
    return resolved


def _load_plugin_object(
    plugin_obj: object, source_label: str, registry: PluginRegistryImpl
) -> None:
    """Register a single plugin object (instance or factory) into *registry*."""
    if callable(plugin_obj) and not isinstance(plugin_obj, type):
        plugin_obj = plugin_obj()
    register_fn = getattr(plugin_obj, "register", None)
    if register_fn is not None:
        log.debug(
            "Loading plugin: %s (from %s)", getattr(plugin_obj, "name", source_label), source_label
        )
        register_fn(registry)
    else:
        log.warning("Plugin object from %s has no register() method, skipping", source_label)


def discover_and_load_plugins(registry: PluginRegistryImpl | None = None) -> PluginRegistryImpl:
    """Discover installed plugins via entry points and populate *registry*.

    Always rescans. Long-lived servers should call :func:`ensure_plugins_loaded`
    instead, so a polled route does not repeat the scan on every request.
    """
    global _global_registry, _discovery_completed  # noqa: PLW0603

    if registry is None:
        registry = PluginRegistryImpl()

    eps = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    for ep in eps:
        try:
            plugin_obj = ep.load()
            _load_plugin_object(plugin_obj, ep.value, registry)
        except Exception:
            log.exception("Failed to load plugin: %s", ep.name)

    _global_registry = registry
    _discovery_completed = True
    return registry


def ensure_plugins_loaded() -> PluginRegistryImpl:
    """Populate the global registry once per process; later calls are no-ops.

    Entry-point discovery is process-stable: rescanning re-executes every plugin's
    registration and swaps the module-global registry, which is needless churn on a
    request-scoped path and racy under a threaded server.
    """
    if _discovery_completed:
        return _global_registry
    return discover_and_load_plugins()


def reset_plugin_discovery() -> None:
    """Forget that discovery ran, so the next :func:`ensure_plugins_loaded` rescans.

    Exists for tests that install or remove entry points within one process.
    """
    global _discovery_completed  # noqa: PLW0603
    _discovery_completed = False
