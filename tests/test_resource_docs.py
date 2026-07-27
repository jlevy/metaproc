from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from frontmatter_format import FmFormatError

from metaproc.resource_docs import load_resource_md, resource_doc_field


def _make_pkg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, text: str) -> str:
    """Create an importable package with a hello.md and put it on sys.path.

    Each test uses a unique package name so Python's module cache doesn't serve
    a stale package from a prior tmp_path.
    """
    pkg = tmp_path / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "hello.md").write_text(text, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    return name


def test_load_resource_md_reads_and_appends_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = _make_pkg(tmp_path, monkeypatch, "rdpkg_read", "# Hello\n")
    assert load_resource_md(pkg, "hello") == "# Hello\n"
    assert load_resource_md(pkg, "hello.md") == "# Hello\n"


def test_load_resource_md_strips_valid_frontmatter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = _make_pkg(
        tmp_path,
        monkeypatch,
        "rdpkg_frontmatter",
        "---\ntitle: Hello\n---\n# Hello\n",
    )
    assert load_resource_md(pkg, "hello") == "# Hello\n"


def test_load_resource_md_rejects_unclosed_frontmatter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = _make_pkg(
        tmp_path,
        monkeypatch,
        "rdpkg_unclosed_frontmatter",
        "---\ntitle: Hello\n# Hello\n",
    )
    with pytest.raises(FmFormatError, match="end of frontmatter not found"):
        load_resource_md(pkg, "hello")


def test_load_resource_md_rejects_invalid_yaml_frontmatter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = _make_pkg(
        tmp_path,
        monkeypatch,
        "rdpkg_invalid_frontmatter",
        "---\ntitle: [unterminated\n---\n# Hello\n",
    )
    with pytest.raises(FmFormatError, match="flow sequence"):
        load_resource_md(pkg, "hello")


def test_load_resource_md_unknown_doc_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = _make_pkg(tmp_path, monkeypatch, "rdpkg_missing", "# Hello\n")
    with pytest.raises(ValueError, match="Unknown doc"):
        load_resource_md(pkg, "nope")


def test_load_resource_md_unknown_package_raises() -> None:
    with pytest.raises(ValueError, match="Unknown doc"):
        load_resource_md("metaproc_no_such_pkg_xyz", "hello")


def test_resource_doc_field_lazy_loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pkg = _make_pkg(tmp_path, monkeypatch, "rdpkg_field", "# Topic\n")

    @dataclass(frozen=True)
    class Topics:
        topic: str = resource_doc_field(pkg, "hello")

    assert Topics().topic == "# Topic\n"
