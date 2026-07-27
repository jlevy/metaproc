"""Metaproc-domain plugin for metabrowser.

Ships JS renderers + a Python sidekick for metaproc-specific file kinds
(process-spec, resource-report, runpool-log, process-log).

The package's ``plugin/`` subdirectory contains the metabrowser plugin —
``manifest.toml`` declares the kinds and views; ``index.js`` self-registers
the renderers via ``window.metabrowser``. The Python sidekick mounted by
metabrowser provides the data-hook handlers for the views that need
server-side parsing (process-spec viz model, runpool event aggregation, …).

The ``metabrowser.plugins`` entry point in ``pyproject.toml`` points at
``plugin_dir``; metabrowser resolves it via ``importlib.resources`` and
serves the plugin's static assets under ``/plugin-static/metaproc/…``.
"""

from __future__ import annotations

from pathlib import Path

from metaproc.metabrowser_plugin.log_adapters import register_log_adapters

register_log_adapters()


def plugin_dir() -> Path:
    """Return the directory containing the packaged browser assets."""
    return Path(__file__).with_name("plugin")


__all__ = ["plugin_dir", "register_log_adapters"]
