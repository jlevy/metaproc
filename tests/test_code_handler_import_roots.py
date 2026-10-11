"""Import roots for file-based code handlers.

A file handler imports from its own directory, its process directory, and the
project roots above them. The innermost root must win, and a checkout nested
inside another checkout (a linked worktree or a submodule) must import only its
own code, never the enclosing checkout's.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from metaproc.engine.code_handler import resolve_code_handler
from metaproc.models.authored import ProcessStep, StepContext

# Every top-level name these tests import. Each test forgets them so a module
# cached by one layout cannot satisfy an import in the next.
_TEST_PACKAGES = frozenset(
    {
        "mp_roots_scripts",
        "mp_roots_outer_only",
        "mp_roots_shared",
        "mp_roots_repo",
        "mp_roots_sub",
        "mp_roots_near",
    }
)


@pytest.fixture(autouse=True)
def _forget_test_packages() -> Iterator[None]:
    def forget() -> None:
        for name in list(sys.modules):
            if name.split(".", 1)[0] in _TEST_PACKAGES:
                del sys.modules[name]

    forget()
    yield
    forget()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _project(root: Path) -> None:
    _write(root / "pyproject.toml", "[project]\nname = 'demo'\nversion = '0.0.0'\n")


def _checkout(root: Path, *, linked: bool) -> None:
    """Make ``root`` a repository root: ``.git`` is a directory in a primary
    checkout and a file in a linked worktree or submodule."""
    _project(root)
    if linked:
        _write(root / ".git", "gitdir: ../../.git/worktrees/launch\n")
    else:
        (root / ".git").mkdir(parents=True)


def _package(root: Path, name: str, value: str) -> None:
    _write(root / name / "__init__.py", f"VALUE = {value!r}\n")


def _handler(path: Path, imports: str) -> None:
    _write(path, f"{imports}\n\ndef run(context, step):\n    return VALUE\n")


def _call(process_dir: Path, ref: str) -> object:
    fn = resolve_code_handler(ref, process_dir)
    return fn(StepContext({}), cast("ProcessStep", cast("object", None)))


def _nested_launch_checkout(tmp_path: Path) -> tuple[Path, Path]:
    """A primary checkout with a linked worktree created inside it."""
    outer = tmp_path / "main"
    _checkout(outer, linked=False)
    inner = outer / ".claude" / "worktrees" / "launch"
    _checkout(inner, linked=True)
    return outer, inner


@pytest.mark.parametrize("namespace", [True, False], ids=["namespace", "regular"])
def test_nested_checkout_imports_its_own_package_over_the_enclosing_one(
    tmp_path: Path, namespace: bool
) -> None:
    """The enclosing checkout carries an older revision of a package the nested
    checkout also has; the handler must load the nested checkout's revision."""
    outer, inner = _nested_launch_checkout(tmp_path)
    for root in (outer, inner):
        if not namespace:
            _write(root / "mp_roots_scripts" / "__init__.py", "")
    _write(outer / "mp_roots_scripts" / "sync.py", "def older_helper():\n    return 'outer'\n")
    _write(
        inner / "mp_roots_scripts" / "sync.py", "def build_tree_manifest():\n    return 'inner'\n"
    )
    stages = inner / "pipeline" / "process" / "stages"
    _handler(
        stages / "boundaries.py",
        "from mp_roots_scripts.sync import build_tree_manifest\nVALUE = build_tree_manifest()",
    )

    assert _call(stages / "depth", "../boundaries.py:run") == "inner"


@pytest.mark.parametrize(
    ("outer_module", "module_name"),
    [
        ("mp_roots_outer_only/__init__.py", "mp_roots_outer_only"),
        ("mp_roots_scripts/outer_only.py", "mp_roots_scripts.outer_only"),
    ],
    ids=["top-level-package", "namespace-portion"],
)
@pytest.mark.parametrize("process_subdir", ["pipeline/process", "."], ids=["nested", "at-root"])
def test_nested_checkout_cannot_import_from_the_enclosing_checkout(
    tmp_path: Path, outer_module: str, module_name: str, process_subdir: str
) -> None:
    """Code that exists only in the enclosing checkout stays unimportable, including
    a module the enclosing checkout adds to a namespace package both share."""
    outer, inner = _nested_launch_checkout(tmp_path)
    _write(outer / outer_module, "VALUE = 'outer'\n")
    _write(inner / "mp_roots_scripts" / "sync.py", "VALUE = 'inner'\n")
    process_dir = (inner / process_subdir).resolve()
    _handler(process_dir / "handler.py", f"import {module_name}\nVALUE = 'imported'")

    with pytest.raises(ModuleNotFoundError, match=re.escape(f"No module named '{module_name}'")):
        resolve_code_handler("handler.py:run", process_dir)


def test_single_repository_imports_every_project_root_innermost_first(tmp_path: Path) -> None:
    """Inside one repository, a sub-project root and the repository root both stay
    importable; where they share a name, the root nearer the handler wins, and a
    module beside the handler wins over both."""
    repo = tmp_path / "repo"
    _checkout(repo, linked=False)
    sub = repo / "subproject"
    _project(sub)
    process_dir = sub / "process" / "mine"
    _package(repo, "mp_roots_repo", "repo")
    _package(sub, "mp_roots_sub", "sub")
    for root, value in ((repo, "repo"), (sub, "sub")):
        _package(root, "mp_roots_shared", value)
        _package(root, "mp_roots_near", value)
    _write(process_dir / "mp_roots_near.py", "VALUE = 'handler'\n")
    _handler(
        process_dir / "handler.py",
        "from mp_roots_repo import VALUE as REPO\n"
        "from mp_roots_sub import VALUE as SUB\n"
        "from mp_roots_shared import VALUE as SHARED\n"
        "from mp_roots_near import VALUE as NEAR\n"
        "VALUE = (REPO, SUB, SHARED, NEAR)",
    )

    assert _call(process_dir, "handler.py:run") == ("repo", "sub", "sub", "handler")


def test_tree_without_repository_metadata_keeps_every_project_root(tmp_path: Path) -> None:
    """With no ``.git`` above the process (a source bundle, for example) there is no
    repository boundary, so every enclosing project root stays importable,
    innermost first."""
    outer = tmp_path / "bundle"
    _project(outer)
    inner = outer / "subproject"
    _project(inner)
    process_dir = inner / "process"
    _package(outer, "mp_roots_outer_only", "outer")
    for root, value in ((outer, "outer"), (inner, "inner")):
        _package(root, "mp_roots_shared", value)
    _handler(
        process_dir / "handler.py",
        "from mp_roots_outer_only import VALUE as OUTER\n"
        "from mp_roots_shared import VALUE as SHARED\n"
        "VALUE = (OUTER, SHARED)",
    )

    assert _call(process_dir, "handler.py:run") == ("outer", "inner")


def test_handler_import_roots_leave_sys_path_unchanged(tmp_path: Path) -> None:
    _outer, inner = _nested_launch_checkout(tmp_path)
    process_dir = inner / "process"
    _handler(process_dir / "handler.py", "VALUE = 'ok'")
    before = list(sys.path)

    assert _call(process_dir, "handler.py:run") == "ok"
    assert sys.path == before
