"""Code-mode handler resolution."""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import importlib.util
import inspect
import sys
import types
from collections.abc import Generator
from pathlib import Path
from typing import cast

from metaproc.engine.placeholders import resolve_templates
from metaproc.models.authored import CodeHandler

__all__ = ["CodeHandler", "resolve_code_handler", "resolve_templates"]


def _looks_like_file_path(ref: str) -> bool:
    """A handler ref is a file path if it ends in .py or contains a path separator."""
    return ref.endswith(".py") or "/" in ref


def resolve_code_handler(
    handler_ref: str,
    process_dir: Path,
) -> CodeHandler:
    """Load a code-mode handler from a file path OR an installed module path.

    Supported forms of ``handler_ref`` (both use ``:`` to separate the
    module/file from the function name):

    - File path: ``"file.py:function_name"`` or ``"../relative/file.py:fn"``
      — resolved relative to ``process_dir`` and loaded via
      ``importlib.util.spec_from_file_location``. Use for handlers that
      live next to a process spec.
    - Module path: ``"package.module.path:function_name"`` — resolved via
      ``importlib.import_module``. Use for handlers that live in an
      installed package (e.g. ``example_plugin.qa.handler:check_handler``).

    The form is inferred from the ref itself: a ``.py`` suffix or any
    ``/`` forces file-path resolution; anything else is treated as a
    dotted module path.
    """
    if ":" not in handler_ref:
        msg = (
            f"handler '{handler_ref}' must be 'file.py:function_name' "
            f"or 'package.module:function_name'"
        )
        raise ValueError(msg)

    ref_part, func_name = handler_ref.rsplit(":", 1)

    if _looks_like_file_path(ref_part):
        module = _load_from_file(ref_part, process_dir)
    else:
        module = _load_from_module_path(ref_part)

    func = getattr(module, func_name, None)
    if func is None:
        available = [
            n for n in dir(module) if not n.startswith("_") and callable(getattr(module, n))
        ]
        msg = f"function '{func_name}' not found in {module.__name__}; available: {available}"
        raise AttributeError(msg)

    if not callable(func):
        msg = f"'{func_name}' in {module.__name__} is not callable"
        raise TypeError(msg)

    sig = inspect.signature(func)
    params = [p for p in sig.parameters.values() if p.default is inspect.Parameter.empty]
    if len(params) < 2:
        msg = (
            f"handler '{func_name}' in {module.__name__} must accept at least 2 parameters "
            f"(context, step), got {len(params)}"
        )
        raise TypeError(msg)

    return cast("CodeHandler", func)


def _load_from_file(file_part: str, process_dir: Path) -> types.ModuleType:
    handler_path = (process_dir / file_part).resolve()
    if not handler_path.is_file():
        msg = f"handler file not found: {handler_path}"
        raise FileNotFoundError(msg)

    with _handler_import_roots_ctx(process_dir, handler_path):
        # Disambiguate by full path: registration in sys.modules is authoritative
        # (below), so two handler files sharing a stem (e.g. `handler.py` across
        # composite steps) must not collide on the same module key.
        digest = hashlib.sha1(str(handler_path).encode()).hexdigest()[:8]
        spec = importlib.util.spec_from_file_location(
            f"_handler_{handler_path.stem}_{digest}",
            handler_path,
        )
        if spec is None or spec.loader is None:
            msg = f"cannot load handler file: {handler_path}"
            raise ImportError(msg)

        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def _handler_import_roots_ctx(process_dir: Path, handler_path: Path) -> Generator[None]:
    """Temporarily put the handler's import roots at the front of ``sys.path``, in
    precedence order, restoring it afterward."""
    roots = _handler_import_roots(process_dir, handler_path)
    added: list[str] = []
    # Insert lowest precedence first so the first root ends up at sys.path[0].
    for candidate in reversed(roots):
        root_str = str(candidate)
        if root_str not in sys.path:
            sys.path.insert(0, root_str)
            added.append(root_str)
    try:
        yield
    finally:
        for root_str in added:
            with contextlib.suppress(ValueError):
                sys.path.remove(root_str)


def _handler_import_roots(process_dir: Path, handler_path: Path) -> list[Path]:
    """Return the ``sys.path`` roots for a file-based handler, highest precedence first.

    The handler's directory and the process directory come first, then each project
    root enclosing the process directory (a directory holding ``pyproject.toml`` or
    ``.git``), innermost first, so the nearest root wins when two define the same name.

    The walk stops at the innermost repository root, the nearest directory holding a
    ``.git`` entry: a directory in a primary checkout, a file in a linked worktree or
    submodule. A checkout nested inside another therefore imports only its own code,
    never a different revision from the enclosing checkout. Without repository
    metadata, as in a source bundle, every enclosing project root is included.
    """
    roots: list[Path] = []
    for path in (handler_path.parent.resolve(), process_dir.resolve()):
        if path not in roots:
            roots.append(path)

    process_root = process_dir.resolve()
    for candidate in (process_root, *process_root.parents):
        repository_root = (candidate / ".git").exists()
        if candidate not in roots and (repository_root or (candidate / "pyproject.toml").exists()):
            roots.append(candidate)
        if repository_root:
            break
    return roots


def _load_from_module_path(module_path: str) -> types.ModuleType:
    try:
        return importlib.import_module(module_path)
    except ImportError as exc:
        msg = f"cannot import handler module '{module_path}': {exc}"
        raise ImportError(msg) from exc
