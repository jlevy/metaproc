"""Tests for ``metaproc skill`` command and skill registry/compose machinery."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.skill.compose import compose_skill
from metaproc.skill.registry import get_skill, iter_skills

runner = CliRunner()


# ---------------------------------------------------------------------------
# Registry discovery
# ---------------------------------------------------------------------------


def test_registry_discovers_metaproc_skill() -> None:
    """The metaproc skill is registered via entry points and discoverable."""
    skills = list(iter_skills())
    names = [s.name for s in skills]
    assert "metaproc" in names


def test_get_skill_returns_metaproc() -> None:
    spec = get_skill("metaproc")
    assert spec is not None
    assert spec.name == "metaproc"
    assert spec.description  # non-empty


def test_get_skill_returns_none_for_unknown() -> None:
    assert get_skill("nonexistent-skill-xyz") is None


# ---------------------------------------------------------------------------
# compose_skill
# ---------------------------------------------------------------------------


def test_compose_produces_yaml_frontmatter() -> None:
    spec = get_skill("metaproc")
    assert spec is not None
    composed = compose_skill(spec)
    # Must start with ---
    assert composed.startswith("---\n")
    # Must have closing ---
    lines = composed.split("\n")
    # Find end of frontmatter
    second_dash = lines[1:].index("---") + 1
    assert second_dash > 0
    frontmatter_block = "\n".join(lines[1:second_dash])
    assert "name:" in frontmatter_block
    assert "description:" in frontmatter_block


def test_compose_has_do_not_edit_marker() -> None:
    spec = get_skill("metaproc")
    assert spec is not None
    composed = compose_skill(spec)
    assert "DO NOT EDIT" in composed
    assert "metaproc skill metaproc --install" in composed


def test_compose_includes_baseline_body() -> None:
    spec = get_skill("metaproc")
    assert spec is not None
    composed = compose_skill(spec)
    # The baseline mentions "Metaproc Orchestration Patterns" or similar content
    assert "metaproc" in composed.lower()


def test_compose_includes_help_topic_catalog() -> None:
    """The catalog section lists metaproc help topics."""
    spec = get_skill("metaproc")
    assert spec is not None
    composed = compose_skill(spec)
    # Must include the help topics from HelpTopics
    assert "operator" in composed
    assert "developer" in composed
    assert "concepts" in composed


def test_compose_is_deterministic() -> None:
    spec = get_skill("metaproc")
    assert spec is not None
    first = compose_skill(spec)
    second = compose_skill(spec)
    assert first == second


def test_compose_includes_allowed_tools_when_present() -> None:
    spec = get_skill("metaproc")
    assert spec is not None
    composed = compose_skill(spec)
    if spec.allowed_tools:
        assert "allowed-tools:" in composed


# ---------------------------------------------------------------------------
# CLI: metaproc skill --list
# ---------------------------------------------------------------------------


def test_skill_list_shows_metaproc() -> None:
    result = runner.invoke(app, ["skill", "--list"])
    assert result.exit_code == 0
    assert "metaproc" in result.stdout


def test_skill_list_shows_description() -> None:
    result = runner.invoke(app, ["skill", "--list"])
    assert result.exit_code == 0
    spec = get_skill("metaproc")
    assert spec is not None
    # At least part of the description should appear
    assert spec.description[:30] in result.stdout


# ---------------------------------------------------------------------------
# CLI: metaproc skill <name>
# ---------------------------------------------------------------------------


def test_skill_print_metaproc() -> None:
    result = runner.invoke(app, ["skill", "metaproc"])
    assert result.exit_code == 0
    assert "---" in result.stdout
    assert "DO NOT EDIT" in result.stdout


def test_skill_print_unknown_errors() -> None:
    result = runner.invoke(app, ["skill", "nonexistent"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# CLI: metaproc skill <name> --install
# ---------------------------------------------------------------------------


def test_skill_install_writes_both_portable_and_claude(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["skill", "metaproc", "--install"])
    assert result.exit_code == 0

    portable = tmp_path / ".agents" / "skills" / "metaproc" / "SKILL.md"
    mirror = tmp_path / ".claude" / "skills" / "metaproc" / "SKILL.md"
    assert portable.exists(), "portable .agents/skills/ path must be written"
    assert mirror.exists(), ".claude/skills/ mirror must be written"
    # Same payload in both (copy, not symlink).
    content = portable.read_text()
    assert content == mirror.read_text()
    assert not mirror.is_symlink()
    assert "DO NOT EDIT" in content
    assert "---" in content


def test_skill_install_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result1 = runner.invoke(app, ["skill", "metaproc", "--install"])
    assert result1.exit_code == 0
    result2 = runner.invoke(app, ["skill", "metaproc", "--install"])
    assert result2.exit_code == 0

    skill_file = tmp_path / ".claude" / "skills" / "metaproc" / "SKILL.md"
    assert skill_file.exists()
