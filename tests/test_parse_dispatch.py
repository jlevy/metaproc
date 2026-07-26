"""Tests for engine.parse_dispatch — parse config → value dispatch.

Phase 2A of the process-file-design-cleanups spec. Exercises the pure parse
function that turns a file's bytes into a value of the declared ``as:`` type.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from metaproc.engine.parse_dispatch import ParseError, parse_input_file
from metaproc.models.authored import ParseConfig, ValueType


@pytest.fixture
def tmp_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "kb-index.yaml"
    p.write_text(
        textwrap.dedent("""\
            version: 1
            tickers:
              - AAPL
              - MSFT
            """)
    )
    return p


@pytest.fixture
def tmp_items_file(tmp_path: Path) -> Path:
    p = tmp_path / "tickers.md"
    p.write_text(
        textwrap.dedent("""\
            ---
            progress:
              schema: metaproc:ProgressSpec/0.1
              process: predict
              run_id: test-run
              items:
                - ticker: AAPL
                  sector: technology
                - ticker: MSFT
                  sector: technology
            ---

            # Items
            """)
    )
    return p


class TestYamlDispatch:
    def test_parses_map(self, tmp_yaml: Path):
        value = parse_input_file(
            tmp_yaml,
            ParseConfig(format="yaml"),
            ValueType.parse("map"),
        )
        assert isinstance(value, dict)
        assert value["version"] == 1
        assert value["tickers"] == ["AAPL", "MSFT"]

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(ParseError, match="does not exist"):
            parse_input_file(
                tmp_path / "missing.yaml",
                ParseConfig(format="yaml"),
                ValueType.parse("map"),
            )

    def test_malformed_yaml_raises(self, tmp_path: Path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("this: is: not: valid: yaml")
        with pytest.raises(ParseError, match="YAML"):
            parse_input_file(bad, ParseConfig(format="yaml"), ValueType.parse("map"))


class TestFrontmatterDispatch:
    def test_extract_items_returns_list_of_maps(self, tmp_items_file: Path):
        value = parse_input_file(
            tmp_items_file,
            ParseConfig(format="frontmatter-md", extract="items"),
            ValueType.parse("list<map>"),
        )
        assert isinstance(value, list)
        assert len(value) == 2
        assert value[0]["ticker"] == "AAPL"
        assert value[1]["ticker"] == "MSFT"

    def test_no_extract_returns_full_frontmatter(self, tmp_items_file: Path):
        value = parse_input_file(
            tmp_items_file,
            ParseConfig(format="frontmatter-md"),
            ValueType.parse("map"),
        )
        assert isinstance(value, dict)
        # The entire frontmatter mapping is returned.
        assert "progress" in value

    def test_extract_from_file_without_frontmatter_raises(self, tmp_path: Path):
        p = tmp_path / "plain.md"
        p.write_text("# Just markdown\n")
        with pytest.raises(ParseError, match="frontmatter"):
            parse_input_file(
                p,
                ParseConfig(format="frontmatter-md", extract="items"),
                ValueType.parse("list<map>"),
            )


class TestTypeMismatch:
    def test_list_declaration_on_map_value_raises(self, tmp_yaml: Path):
        """Declaring ``as: list<map>`` on a file whose value is a dict fails."""
        with pytest.raises(ParseError, match="expected list"):
            parse_input_file(
                tmp_yaml,
                ParseConfig(format="yaml"),
                ValueType.parse("list<map>"),
            )

    def test_map_declaration_on_list_value_raises(self, tmp_path: Path):
        p = tmp_path / "list.yaml"
        p.write_text("- a\n- b\n")
        with pytest.raises(ParseError, match="expected map"):
            parse_input_file(
                p,
                ParseConfig(format="yaml"),
                ValueType.parse("map"),
            )

    def test_list_element_shape_is_checked_recursively(self, tmp_path: Path):
        p = tmp_path / "mixed-list.yaml"
        p.write_text("- ticker: AAPL\n- not-a-map\n")
        with pytest.raises(ParseError, match=r"\[1\].*expected map"):
            parse_input_file(
                p,
                ParseConfig(format="yaml"),
                ValueType.parse("list<map>"),
            )

    def test_map_value_shape_is_checked_recursively(self, tmp_path: Path):
        p = tmp_path / "typed-map.yaml"
        p.write_text("report: summary.md\ncount: 2\n")
        with pytest.raises(ParseError, match=r"\.count.*expected path"):
            parse_input_file(
                p,
                ParseConfig(format="yaml"),
                ValueType.parse("map<string, path>"),
            )
