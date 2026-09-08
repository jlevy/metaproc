"""Fail when a shipped document's ``last updated`` date is older than its last real edit.

``docs/project/README.md`` asks contributors to bump the date in a document's header
when they change it substantively. Nothing enforced that, and the drift was real: at the
time this check was written ``arch-claude-code-harness.md`` claimed 2026-05-23 against
commits three months newer, and the design doc's own header claimed a revision two
behind its newest history entry.

A stale date is worse now that these documents ship in the wheel: it is a claim about
currency made to every downstream reader, not just to contributors.

What counts as a real edit: a non-merge commit that changes something other than
whitespace. Reflows from ``make format`` are ignored, because Flowmark rewraps
paragraphs across the whole tree and would otherwise demand a date bump on every
document at once. Renames are followed, so moving a document does not reset its history.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHIPPED_DIR = ROOT / "src" / "metaproc" / "docs"

# "**Date:** 2026-03-23 (last updated 2026-08-25)" — the convention in the arch docs.
LAST_UPDATED_RE = re.compile(r"last updated (\d{4}-\d{2}-\d{2})")


def _git(cwd: Path, *args: str) -> str:
    """Run git in ``cwd`` and return stdout, or "" if the command fails."""
    try:
        result = subprocess.run(("git", *args), cwd=cwd, capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, OSError):
        return ""
    return result.stdout


def _repo_root(path: Path) -> Path | None:
    """Return the git root containing ``path``, or None when it is not tracked.

    Derived from the file rather than assumed, so the check runs the same against the
    repository and against a fixture built in a temporary directory.
    """
    top = _git(path.parent, "rev-parse", "--show-toplevel").strip()
    return Path(top) if top else None


def _normalized(text: str) -> str:
    """Collapse every whitespace run to one space, so a reflow compares equal."""
    return " ".join(text.split())


def _history(root: Path, relative: str) -> list[tuple[str, date, str]]:
    """Return (sha, date, path-at-that-commit) newest first, following renames."""
    log = _git(
        root,
        "log",
        "--follow",
        "--no-merges",
        "--format=%x1e%H %ad",
        "--name-only",
        "--date=short",
        "--",
        relative,
    )
    records: list[tuple[str, date, str]] = []
    for chunk in log.split("\x1e"):
        lines = [line for line in chunk.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        sha, _, datestr = lines[0].partition(" ")
        records.append((sha, date.fromisoformat(datestr.strip()), lines[-1].strip()))
    return records


def last_substantive_commit(path: Path) -> tuple[str, date] | None:
    """Return the newest commit that changed ``path`` other than by reflowing it.

    Compares each commit's content against the previous commit's, with whitespace
    normalized away. ``git diff -w`` is not enough here: Flowmark rewraps paragraphs,
    which moves words between lines, and ``-w`` only ignores whitespace *within* a line.
    Without this, `make format` would invalidate every document's date at once and the
    check would train people to bump dates without meaning it.

    Returns ``None`` when the file is not in a git repository.
    """
    root = _repo_root(path)
    if root is None:
        return None
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    records = _history(root, relative)
    for index, (sha, committed, path_then) in enumerate(records):
        current = _normalized(_git(root, "show", f"{sha}:{path_then}"))
        if index + 1 < len(records):
            prev_sha, _, prev_path = records[index + 1]
            previous = _normalized(_git(root, "show", f"{prev_sha}:{prev_path}"))
        else:
            previous = ""  # the commit that introduced the file
        if current != previous:
            return sha, committed
    return None


def check_doc_dates(shipped_dir: Path = SHIPPED_DIR) -> list[str]:
    """Return one finding per document whose ``last updated`` date has fallen behind."""
    findings: list[str] = []
    for source in sorted(shipped_dir.glob("*.md")):
        match = LAST_UPDATED_RE.search(source.read_text("utf-8"))
        if match is None:
            # Not every shipped document carries the marker; only the arch-style ones
            # do, and this check does not invent the convention for the others.
            continue
        claimed = date.fromisoformat(match.group(1))
        latest = last_substantive_commit(source)
        if latest is None:
            continue
        sha, committed = latest
        root = _repo_root(source)
        shown = source.resolve().relative_to(root.resolve()) if root else source.name
        if committed > claimed:
            findings.append(
                f"{shown}: claims 'last updated {claimed}' but was "
                f"changed on {committed} in {sha[:9]}"
            )
    return findings


def main() -> int:
    """Print findings and return a process exit code."""
    findings = check_doc_dates()
    if findings:
        sys.stderr.write("Shipped-doc date check failed:\n")
        for finding in findings:
            sys.stderr.write(f"- {finding}\n")
        sys.stderr.write(
            "Bump the 'last updated' date in each document's header to the date of the change.\n"
        )
        return 1
    sys.stdout.write("Shipped-doc date checks passed.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
