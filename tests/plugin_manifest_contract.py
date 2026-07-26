"""Parse plugin manifests for the Node browser-contract shims."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import TypedDict, cast


class PluginManifestContract(TypedDict):
    """Manifest fields consumed by browser-contract tests."""

    extra_scripts: list[str]
    views: list[dict[str, str]]


def _string_list(value: object, *, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list of strings")
    items = cast("list[object]", value)
    if not all(isinstance(item, str) for item in items):
        raise ValueError(f"{field} must be a list of strings")
    return cast("list[str]", items)


def load_manifest_contract(plugin_root: Path) -> PluginManifestContract:
    """Load the script order and declared views with Python's TOML parser."""
    manifest = cast(
        "dict[str, object]",
        tomllib.loads((plugin_root / "manifest.toml").read_text()),
    )
    plugin_value = manifest.get("plugin")
    if not isinstance(plugin_value, dict):
        raise ValueError(f"{plugin_root}/manifest.toml has no [plugin] table")
    plugin = cast("dict[str, object]", plugin_value)

    views: list[dict[str, str]] = []
    views_value = manifest.get("view")
    if isinstance(views_value, list):
        for view_value in cast("list[object]", views_value):
            if not isinstance(view_value, dict):
                continue
            view = cast("dict[str, object]", view_value)
            kind = view.get("kind")
            view_id = view.get("id")
            if isinstance(kind, str) and isinstance(view_id, str):
                views.append({"kind": kind, "id": view_id})

    return {
        "extra_scripts": _string_list(
            plugin.get("extra_scripts"),
            field=f"{plugin_root}/manifest.toml plugin.extra_scripts",
        ),
        "views": views,
    }


def manifest_contracts_json(
    metabrowser_root: Path,
    metaproc_plugin_root: Path,
    *,
    extra_dirs: tuple[Path, ...] = (),
) -> str:
    """Serialize every plugin manifest that a Node shim will load."""
    plugin_roots: list[Path] = []
    builtin_root = metabrowser_root / "builtin_plugins"
    if builtin_root.is_dir():
        plugin_roots.extend(
            child
            for child in sorted(builtin_root.iterdir())
            if child.is_dir()
            and not child.name.startswith(("_", "."))
            and (child / "manifest.toml").is_file()
            and (child / "index.js").is_file()
        )
    if (metaproc_plugin_root / "manifest.toml").is_file():
        plugin_roots.append(metaproc_plugin_root)
    for extra_dir in extra_dirs:
        if not extra_dir.is_dir():
            continue
        plugin_roots.extend(
            child
            for child in sorted(extra_dir.iterdir())
            if child.is_dir()
            and not child.name.startswith(("_", "."))
            and (child / "manifest.toml").is_file()
            and (child / "index.js").is_file()
        )

    contracts = {
        str(plugin_root.resolve()): load_manifest_contract(plugin_root)
        for plugin_root in plugin_roots
    }
    return json.dumps(contracts, sort_keys=True)
