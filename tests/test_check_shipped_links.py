"""Contracts for the gate that separates repository-valid links from wheel-valid ones.

``devtools/check_links.py`` resolves local links against the repository root, so a
shipped document linking ``../../../docs/development.md`` passes there while being dead
for every reader of the installed package. This gate exists for exactly that gap, so
these cases pin the distinction rather than link validity in general.
"""

from __future__ import annotations

from pathlib import Path

from devtools.check_shipped_links import check_shipped_links


def _shipped(tmp_path: Path, name: str, body: str) -> Path:
    """Lay out a miniature package: src/metaproc/docs/<name> plus a sibling module."""
    docs = tmp_path / "src" / "metaproc" / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / name).write_text(body, "utf-8")
    return docs


def _check(tmp_path: Path) -> list[str]:
    return check_shipped_links(
        shipped_dir=tmp_path / "src" / "metaproc" / "docs",
        package_dir=tmp_path / "src" / "metaproc",
    )


def test_sibling_link_to_an_existing_doc_passes(tmp_path: Path) -> None:
    docs = _shipped(tmp_path, "a.md", "See [b](b.md).\n")
    (docs / "b.md").write_text("# B\n", "utf-8")
    assert _check(tmp_path) == []


def test_link_into_the_package_passes(tmp_path: Path) -> None:
    # Everything under src/metaproc/ ships at the same relative offset in the wheel,
    # so ../runpool/README.md resolves for a wheel reader as well as a repo reader.
    _shipped(tmp_path, "a.md", "See [notes](../runpool/README.md).\n")
    runpool = tmp_path / "src" / "metaproc" / "runpool"
    runpool.mkdir(parents=True)
    (runpool / "README.md").write_text("# RunPool\n", "utf-8")
    assert _check(tmp_path) == []


def test_link_outside_the_package_is_reported(tmp_path: Path) -> None:
    # The failure this gate exists for: valid in a checkout, dead in the wheel.
    _shipped(tmp_path, "a.md", "See [dev](../../../docs/development.md).\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "development.md").write_text("# Dev\n", "utf-8")
    findings = _check(tmp_path)
    assert len(findings) == 1
    assert "escapes the package" in findings[0]


def test_missing_in_package_target_is_reported(tmp_path: Path) -> None:
    _shipped(tmp_path, "a.md", "See [gone](gone.md).\n")
    findings = _check(tmp_path)
    assert len(findings) == 1
    assert "missing in-package link" in findings[0]


def test_absolute_and_anchor_links_are_left_alone(tmp_path: Path) -> None:
    # Absolute URLs are the documented escape hatch; nothing in the repository
    # validates them, which is why rewriting a link away is preferred.
    _shipped(
        tmp_path,
        "a.md",
        "[gh](https://github.com/jlevy/metaproc/blob/main/README.md) "
        "[here](#section) [mail](mailto:someone@example.invalid)\n",
    )
    assert _check(tmp_path) == []


def test_anchor_on_a_sibling_link_resolves_to_the_file(tmp_path: Path) -> None:
    docs = _shipped(tmp_path, "a.md", "See [b](b.md#a-section).\n")
    (docs / "b.md").write_text("# B\n", "utf-8")
    assert _check(tmp_path) == []


def test_the_real_shipped_docs_pass() -> None:
    assert check_shipped_links() == []
