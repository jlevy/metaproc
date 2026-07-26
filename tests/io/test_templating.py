"""Tests for metaproc.io.templating — strict ``{{ }}`` document renderer."""

from __future__ import annotations

import pytest

from metaproc.io.templating import (
    TemplateRenderError,
    render_template,
    strip_template_frontmatter,
)

# ---------------------------------------------------------------------------
# render_template — basic substitution
# ---------------------------------------------------------------------------


class TestRenderTemplateBasic:
    def test_single_placeholder(self) -> None:
        result = render_template("Hello {{name}}!", {"name": "world"})
        assert result == "Hello world!"

    def test_multiple_placeholders(self) -> None:
        result = render_template(
            "{{date}} — {{slug}} batch",
            {"date": "2026-06-02", "slug": "mon-ensemble"},
        )
        assert result == "2026-06-02 — mon-ensemble batch"

    def test_repeated_placeholder(self) -> None:
        result = render_template(
            "{{x}} and {{x}} again",
            {"x": "val"},
        )
        assert result == "val and val again"

    def test_no_placeholders(self) -> None:
        result = render_template("plain text", {})
        assert result == "plain text"

    def test_multiline_template(self) -> None:
        tpl = "# {{title}}\n\nDate: {{date}}\n"
        result = render_template(tpl, {"title": "Report", "date": "2026-01-01"})
        assert result == "# Report\n\nDate: 2026-01-01\n"

    def test_dotted_placeholder(self) -> None:
        result = render_template("{{run.id}}", {"run.id": "abc123"})
        assert result == "abc123"

    def test_adjacent_placeholders(self) -> None:
        result = render_template("{{a}}{{b}}", {"a": "X", "b": "Y"})
        assert result == "XY"


# ---------------------------------------------------------------------------
# render_template — strict mode errors
# ---------------------------------------------------------------------------


class TestRenderTemplateStrictErrors:
    def test_missing_placeholder_raises(self) -> None:
        with pytest.raises(TemplateRenderError, match="not present in values"):
            render_template("Hello {{name}}!", {})

    def test_unused_value_raises(self) -> None:
        with pytest.raises(TemplateRenderError, match="never used as placeholders"):
            render_template("Hello!", {"name": "world"})

    def test_partial_missing_raises(self) -> None:
        with pytest.raises(TemplateRenderError, match="{{b}}"):
            render_template("{{a}} {{b}}", {"a": "X"})

    def test_partial_unused_raises(self) -> None:
        with pytest.raises(TemplateRenderError, match="extra"):
            render_template("{{a}}", {"a": "X", "extra": "Y"})

    def test_both_missing_and_unused_raises_missing_first(self) -> None:
        """When strict, check missing before unused."""
        with pytest.raises(TemplateRenderError, match="not present in values"):
            render_template("{{a}}", {"b": "Y"})


# ---------------------------------------------------------------------------
# render_template — non-strict mode
# ---------------------------------------------------------------------------


class TestRenderTemplateNonStrict:
    def test_unknown_placeholder_left_as_is(self) -> None:
        result = render_template("{{a}} {{b}}", {"a": "X"}, strict=False)
        assert result == "X {{b}}"

    def test_unused_value_ignored(self) -> None:
        result = render_template("{{a}}", {"a": "X", "extra": "Y"}, strict=False)
        assert result == "X"

    def test_all_unknown_left_as_is(self) -> None:
        result = render_template("{{foo}} bar", {}, strict=False)
        assert result == "{{foo}} bar"


# ---------------------------------------------------------------------------
# strip_template_frontmatter
# ---------------------------------------------------------------------------


class TestStripTemplateFrontmatter:
    def test_removes_template_block(self) -> None:
        text = (
            "---\n"
            "template:\n"
            "  status: validated\n"
            "  vars: [date, slug]\n"
            "title: My Doc\n"
            "---\n"
            "# Body\n"
        )
        result = strip_template_frontmatter(text)
        assert "template:" not in result
        assert "title: My Doc" in result
        assert "# Body" in result
        assert result.startswith("---\n")

    def test_preserves_other_frontmatter(self) -> None:
        text = (
            "---\n"
            "title: Plan\n"
            "template:\n"
            "  status: validated\n"
            "  vars: [x]\n"
            "author: test\n"
            "---\n"
            "Body\n"
        )
        result = strip_template_frontmatter(text)
        assert "title: Plan" in result
        assert "author: test" in result
        assert "template:" not in result

    def test_empty_frontmatter_after_strip(self) -> None:
        text = "---\ntemplate:\n  status: validated\n  vars: [x]\n---\n# Body\n"
        result = strip_template_frontmatter(text)
        assert not result.startswith("---")
        assert "# Body" in result

    def test_no_frontmatter_passthrough(self) -> None:
        text = "# Just a heading\n\nSome text.\n"
        result = strip_template_frontmatter(text)
        assert result == text

    def test_no_template_block_passthrough(self) -> None:
        text = "---\ntitle: Doc\n---\n# Body\n"
        result = strip_template_frontmatter(text)
        assert result == text
