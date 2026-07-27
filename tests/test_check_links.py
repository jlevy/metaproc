"""Contracts for the repository-local Markdown link checker."""

from pathlib import Path

from devtools.check_links import check_local_markdown_links


def test_local_link_checker_reports_missing_targets_and_ignores_urls(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "present.md").write_text("# Present\n")
    (docs / "index.md").write_text(
        "\n".join(
            [
                "[present](present.md#section)",
                "[root](/README.md)",
                "[external](https://example.com/missing)",
                "[anchor](#local-heading)",
                "[missing](absent.md)",
            ]
        )
    )
    (tmp_path / "README.md").write_text("# Root\n")

    findings = check_local_markdown_links(tmp_path)

    assert findings == ["docs/index.md:5: missing local link target: absent.md"]


def test_local_link_checker_skips_generated_agent_directories(tmp_path: Path) -> None:
    generated = tmp_path / ".agents" / "skills" / "example"
    generated.mkdir(parents=True)
    (generated / "SKILL.md").write_text("[managed](missing.md)\n")

    assert check_local_markdown_links(tmp_path) == []


def test_local_link_checker_skips_byte_exact_fixtures(tmp_path: Path) -> None:
    fixture = tmp_path / "tests" / "fixtures" / "captured"
    fixture.mkdir(parents=True)
    (fixture / "README.md").write_text("[captured](missing.md)\n")

    assert check_local_markdown_links(tmp_path) == []


def test_local_link_checker_rejects_targets_outside_repository(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.md"
    outside.write_text("# Outside\n")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.md").write_text("[outside](../../outside.md)\n")

    assert check_local_markdown_links(tmp_path) == [
        "docs/index.md:1: local link escapes repository: ../../outside.md"
    ]
