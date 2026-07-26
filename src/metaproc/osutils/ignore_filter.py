"""Gitignore-aware path filtering.

Uses ``pathspec.GitIgnoreSpec`` to match paths against gitignore-format patterns.
Adapted from kash's ``ignore_files.py`` for use in the metabrowser and
any other context where directory walks need to skip ignored paths.
"""

from __future__ import annotations

import logging
import os
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pathspec.gitignore import GitIgnoreSpec

log = logging.getLogger(__name__)


class IgnoreMode(StrEnum):
    """How the browser filters paths.

    - ``default``: Apply ``.gitignore`` rules but always show ``.logs/`` and
      ``.state/`` (operational directories).
    - ``gitignore``: Apply ``.gitignore`` rules strictly — no allowlist overrides.
    - ``show_all``: No filtering — show every file and directory.
    """

    default = "default"
    gitignore = "gitignore"
    show_all = "show_all"


# Directories that are shown even when gitignored, under IgnoreMode.default.
ALLOWLIST_DIRS = frozenset({".logs", ".state"})


class IgnoreFilter(Protocol):
    """Callable that returns True if a path should be ignored."""

    def __call__(self, path: str | Path, *, is_dir: bool = False) -> bool: ...


class IgnoreChecker:
    """Check paths against gitignore-format patterns via ``pathspec``."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.spec = GitIgnoreSpec.from_lines(lines)

    @classmethod
    def from_file(cls, path: Path) -> IgnoreChecker:
        """Load patterns from a gitignore-format file."""
        with open(path) as f:
            lines = f.readlines()
        log.info("Loaded ignore patterns (%s lines) from %s", len(lines), path)
        return cls(lines)

    def matches(self, path: str | Path, *, is_dir: bool = False) -> bool:
        """Return True if *path* matches the ignore spec."""
        path_str = str(path)
        if path_str == ".":
            return False
        # Directories need a trailing slash to match gitignore dir patterns.
        patterns = [path_str]
        if is_dir and not path_str.endswith("/"):
            patterns.append(path_str + "/")
        return any(self.spec.match_file(p) for p in patterns)

    def __call__(self, path: str | Path, *, is_dir: bool = False) -> bool:
        return self.matches(path, is_dir=is_dir)

    def __repr__(self) -> str:
        active = [
            line.strip() for line in self.lines if line.strip() and not line.strip().startswith("#")
        ]
        return f"IgnoreChecker({'; '.join(active)})"


ignore_none: IgnoreFilter = lambda path, *, is_dir=False: False
"""No-op filter that ignores nothing."""


def load_gitignore(root: Path) -> IgnoreFilter:
    """Load ``.gitignore`` files from *root* and its subdirectories.

    Collects patterns from the root ``.gitignore`` and any nested ``.gitignore``
    files, prefixing nested patterns with their relative directory so that
    ``pathspec`` matches them correctly. Returns ``ignore_none`` if no
    ``.gitignore`` files exist.
    """
    all_lines: list[str] = []

    root_gitignore = root / ".gitignore"
    if root_gitignore.is_file():
        with open(root_gitignore) as f:
            all_lines.extend(f.readlines())

    # Walk for nested .gitignore files (skip .git itself).
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        if dirpath == str(root):
            continue  # already handled above
        if ".gitignore" in filenames:
            rel_dir = os.path.relpath(dirpath, root)
            nested_path = Path(dirpath) / ".gitignore"
            with open(nested_path) as f:
                for line in f:
                    stripped = line.strip()
                    # Skip blank lines and comments.
                    if not stripped or stripped.startswith("#"):
                        all_lines.append(line)
                    else:
                        # Prefix pattern with relative directory for correct matching.
                        all_lines.append(f"{rel_dir}/{stripped}\n")

    if not all_lines:
        return ignore_none

    log.info("Loaded gitignore patterns (%s lines) from %s", len(all_lines), root)
    return IgnoreChecker(all_lines)


def make_ignore_filter(root: Path, mode: IgnoreMode) -> IgnoreFilter:
    """Build an ``IgnoreFilter`` for the given mode.

    - ``show_all``: returns ``ignore_none``.
    - ``gitignore``: returns a strict gitignore filter.
    - ``default``: returns a gitignore filter that exempts ``ALLOWLIST_DIRS``.
    """
    if mode is IgnoreMode.show_all:
        return ignore_none

    base = load_gitignore(root)
    if base is ignore_none or mode is IgnoreMode.gitignore:
        return base

    # default mode: wrap the base filter to exempt allowlisted directories at any depth.
    def _default_filter(path: str | Path, *, is_dir: bool = False) -> bool:
        if any(part in ALLOWLIST_DIRS for part in Path(path).parts):
            return False
        return base(path, is_dir=is_dir)

    return _default_filter
