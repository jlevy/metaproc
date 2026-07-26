"""Installed-package contract for the Metaproc MetaBrowser plugin."""

from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import cast

from metaproc.metabrowser_plugin import plugin_dir

ELK_BUNDLE_SHA256 = "48d338d5aeddd9503ccf1d12661c11b5d7d43c6afc5f66c7ddb2ea4170c0f6bf"


def test_plugin_dir_factory_returns_packaged_assets() -> None:
    assert callable(plugin_dir)
    root = plugin_dir()
    assert isinstance(root, Path)
    manifest_path = root / "manifest.toml"
    manifest = tomllib.loads(manifest_path.read_text())
    declared = {
        "index.js",
        "styles.css",
        *manifest["plugin"]["extra_scripts"],
        *manifest["plugin"]["extra_styles"],
    }
    assert declared == {
        "domain_views.css",
        "domain_views.js",
        "elk.bundled.js",
        "index.js",
        "styles.css",
        "viz.css",
        "viz.js",
    }
    assert all((root / asset).is_file() for asset in declared)
    assert (root / "elkjs-license.txt").is_file()
    assert hashlib.sha256((root / "elk.bundled.js").read_bytes()).hexdigest() == ELK_BUNDLE_SHA256
    assert manifest["plugin"]["extra_scripts"] == [
        "elk.bundled.js",
        "viz.js",
        "domain_views.js",
    ]


def test_installed_entry_point_loads_plugin_dir_factory() -> None:
    entry_points = importlib.metadata.entry_points(group="metabrowser.plugins")
    entry_point = next(ep for ep in entry_points if ep.name == "metaproc")
    loaded = entry_point.load()

    assert callable(loaded)
    factory = cast(Callable[[], Path], loaded)
    assert Path(factory()).resolve() == plugin_dir().resolve()


def test_python_sidekick_uses_only_the_public_metabrowser_module() -> None:
    package_root = plugin_dir().parent
    private_imports: list[str] = []
    for source_path in package_root.rglob("*.py"):
        tree = ast.parse(source_path.read_text(), filename=str(source_path))
        for node in ast.walk(tree):
            modules: list[str] = []
            line_number: int | None = None
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
                line_number = node.lineno
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
                line_number = node.lineno
            if line_number is None:
                continue
            private_imports.extend(
                f"{source_path.relative_to(package_root)}:{line_number}:{module}"
                for module in modules
                if module.startswith("metabrowser.")
            )

    assert private_imports == []
