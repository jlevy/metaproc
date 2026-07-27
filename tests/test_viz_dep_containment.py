"""Enforce the one-way dependency between engine, models.viz, and the viz module.

- ``metaproc.viz`` may import from ``metaproc.models`` and stdlib only.
- ``metaproc.engine`` may not import from ``metaproc.viz``.

Violations fail at test time rather than at import time so this file is the
single place that names the rules.
"""

from __future__ import annotations

import ast
from pathlib import Path

METAPROC_SRC = Path(__file__).resolve().parents[1] / "src" / "metaproc"

ALLOWED_VIZ_PREFIXES = (
    "metaproc.models",
    "metaproc.viz",
    "metaproc.plugins.protocol",
    "pydantic",
)


def _python_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def _collect_imports(py_file: Path) -> list[str]:
    tree = ast.parse(py_file.read_text())
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return modules


def test_viz_module_has_no_forbidden_imports() -> None:
    viz_dir = METAPROC_SRC / "viz"
    for py_file in _python_files(viz_dir):
        for mod in _collect_imports(py_file):
            if not mod.startswith("metaproc."):
                continue
            if mod.startswith(ALLOWED_VIZ_PREFIXES):
                continue
            msg = (
                f"{py_file.relative_to(METAPROC_SRC)} imports `{mod}` — "
                "metaproc.viz may only depend on metaproc.models and stdlib/pydantic."
            )
            raise AssertionError(msg)


def test_engine_does_not_import_viz() -> None:
    engine_dir = METAPROC_SRC / "engine"
    for py_file in _python_files(engine_dir):
        for mod in _collect_imports(py_file):
            if mod.startswith("metaproc.viz"):
                msg = (
                    f"{py_file.relative_to(METAPROC_SRC)} imports `{mod}` — "
                    "the projection is one-directional: engine → Plan → viz."
                )
                raise AssertionError(msg)
