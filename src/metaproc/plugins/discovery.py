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
    """Discover installed plugins via entry points and populate *registry*."""
    global _global_registry  # noqa: PLW0603

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
    return registry
