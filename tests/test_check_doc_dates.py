"""Contracts for the shipped-doc ``last updated`` drift check.

The convention was stated in the contributor docs and enforced nowhere, and the drift
was real — one architecture doc claimed a date three months behind its newest
substantive commit. Now that these documents ship, that date is a currency claim made to
every downstream reader.

The interesting behavior is what the check *ignores*: `make format` rewraps prose across
the whole tree, and a check that treated a reflow as an edit would demand a date bump on
every document at once, which trains people to bump dates meaninglessly.
"""

from __future__ import annotations

import os
import subprocess
from datetime import date, timedelta
from pathlib import Path

from devtools.check_doc_dates import check_doc_dates, last_substantive_commit


def _run(repo: Path, *args: str) -> None:
    subprocess.run(args, cwd=repo, check=True, capture_output=True)


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    """Build a throwaway git repo with one shipped doc, and return (root, docs)."""
    docs = tmp_path / "src" / "metaproc" / "docs"
    docs.mkdir(parents=True)
    _run(tmp_path, "git", "init", "-q")
    _run(tmp_path, "git", "config", "user.email", "test@example.invalid")
    _run(tmp_path, "git", "config", "user.name", "Test")
    return tmp_path, docs


def _commit(repo: Path, when: date, message: str) -> None:
    stamp = f"{when.isoformat()}T12:00:00"
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", message],
        cwd=repo,
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp},
    )


TODAY = date(2026, 8, 27)
EARLIER = TODAY - timedelta(days=90)


def test_a_current_date_passes(tmp_path: Path) -> None:
    repo, docs = _repo(tmp_path)
    (docs / "arch-x.md").write_text(f"# X\n\n**Date:** (last updated {TODAY})\n", "utf-8")
    _commit(repo, TODAY, "add doc")
    assert check_doc_dates(docs) == []


def test_a_stale_date_is_reported(tmp_path: Path) -> None:
    repo, docs = _repo(tmp_path)
    doc = docs / "arch-x.md"
    doc.write_text(f"# X\n\n**Date:** (last updated {EARLIER})\n", "utf-8")
    _commit(repo, EARLIER, "add doc")
    doc.write_text(f"# X\n\n**Date:** (last updated {EARLIER})\n\nNew section.\n", "utf-8")
    _commit(repo, TODAY, "substantive edit without bumping the date")
    findings = check_doc_dates(docs)
    assert len(findings) == 1
    assert str(EARLIER) in findings[0] and str(TODAY) in findings[0]


def test_a_whitespace_only_commit_does_not_count_as_an_edit(tmp_path: Path) -> None:
    # This is the case that keeps `make format` from invalidating every date at once.
    repo, docs = _repo(tmp_path)
    doc = docs / "arch-x.md"
    doc.write_text(f"# X\n\n**Date:** (last updated {EARLIER})\n\nOne two three.\n", "utf-8")
    _commit(repo, EARLIER, "add doc")
    doc.write_text(f"# X\n\n**Date:** (last updated {EARLIER})\n\nOne two\nthree.\n", "utf-8")
    _commit(repo, TODAY, "reflow")
    assert check_doc_dates(docs) == []


def test_documents_without_the_marker_are_skipped(tmp_path: Path) -> None:
    # Only the arch-style documents carry the convention; the check does not invent it
    # for the operator reference or the concepts docs.
    repo, docs = _repo(tmp_path)
    (docs / "conventions.md").write_text("# Conventions\n\nNo date line here.\n", "utf-8")
    _commit(repo, TODAY, "add doc")
    assert check_doc_dates(docs) == []


def test_last_substantive_commit_is_none_outside_a_repo(tmp_path: Path) -> None:
    stray = tmp_path / "loose.md"
    stray.write_text("# Loose\n", "utf-8")
    assert last_substantive_commit(stray) is None


def test_the_real_shipped_docs_pass() -> None:
    assert check_doc_dates() == []
