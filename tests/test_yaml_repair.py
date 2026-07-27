"""Tests for YAML frontmatter repair."""

from __future__ import annotations

import inspect
from pathlib import Path

from metaproc.engine import yaml_repair
from metaproc.engine.yaml_repair import repair_frontmatter_file
from metaproc.io import fmf_read


class TestRepairFrontmatterFile:
    def test_repairs_inline_notes_with_colons(self, tmp_path: Path) -> None:
        """Values with unquoted colons mid-value should get quoted."""
        record = tmp_path / "record.md"
        record.write_text(
            "---\n"
            "record:\n"
            "  ticker: AAPL\n"
            "  detail: Strong beat (Note: actually Q1 not Q2)\n"
            "  fiscal_quarter: 2025-Q2\n"
            "---\n"
            "# Body\n"
        )
        repaired = repair_frontmatter_file(record)
        assert repaired is True
        content = record.read_text()
        # The value should now be quoted
        assert '"Strong beat (Note: actually Q1 not Q2)"' in content
        # Other lines should be unchanged
        assert "ticker: AAPL" in content
        assert "fiscal_quarter: 2025-Q2" in content

    def test_no_repair_needed(self, tmp_path: Path) -> None:
        """Valid YAML should not be modified."""
        record = tmp_path / "record.md"
        original = "---\nrecord:\n  ticker: AAPL\n  fiscal_quarter: 2025-Q2\n---\n# Body\n"
        record.write_text(original)
        repaired = repair_frontmatter_file(record)
        assert repaired is False
        assert record.read_text() == original

    def test_repairs_value_with_embedded_colon(self, tmp_path: Path) -> None:
        record = tmp_path / "record.md"
        record.write_text(
            "---\n"
            "record:\n"
            "  consensus_error_detail: Large beat suggests operational outperformance. NOTE: Data inconsistency - source shows eps_surprise of 0.04\n"
            "---\n"
            "# Body\n"
        )
        repaired = repair_frontmatter_file(record)
        assert repaired is True

        _, meta = fmf_read(record)
        assert meta is not None

    def test_repairs_plain_scalar_starting_with_quoted_phrase(self, tmp_path: Path) -> None:
        record = tmp_path / "record.md"
        record.write_text(
            "---\n"
            "alt_data_research:\n"
            "  ticker: OLLI\n"
            '  strongest_alt_signal: "Ollies near me" Google Trends accelerated into May.\n'
            "---\n"
            "# Body\n"
        )
        repaired = repair_frontmatter_file(record)
        assert repaired is True

        _, meta = fmf_read(record)
        assert meta is not None
        assert (
            meta["alt_data_research"]["strongest_alt_signal"]
            == '"Ollies near me" Google Trends accelerated into May.'
        )

    def test_repairs_nested_list_value_starting_with_quoted_phrase(self, tmp_path: Path) -> None:
        record = tmp_path / "record.md"
        record.write_text(
            "---\n"
            "alt_data_research:\n"
            "  ticker: M\n"
            "  source_gaps:\n"
            "    - source: google_trends_reimagine\n"
            '      gap: "Macy\'s Reimagine" is sparse across all windows.\n'
            "---\n"
            "# Body\n"
        )
        repaired = repair_frontmatter_file(record)
        assert repaired is True

        _, meta = fmf_read(record)
        assert meta is not None
        assert (
            meta["alt_data_research"]["source_gaps"][0]["gap"]
            == '"Macy\'s Reimagine" is sparse across all windows.'
        )

    def test_preserves_already_quoted_values(self, tmp_path: Path) -> None:
        record = tmp_path / "record.md"
        original = '---\nrecord:\n  detail: "Already quoted: with colon"\n---\n# Body\n'
        record.write_text(original)
        repaired = repair_frontmatter_file(record)
        assert repaired is False

    def test_preserves_yaml_comments(self, tmp_path: Path) -> None:
        """Comments with colons should not trigger repair."""
        record = tmp_path / "record.md"
        original = "---\nrecord:\n  ticker: AAPL  # Note: this is a comment\n---\n# Body\n"
        record.write_text(original)
        repaired = repair_frontmatter_file(record)
        assert repaired is False

    def test_returns_false_if_file_missing(self, tmp_path: Path) -> None:
        record = tmp_path / "nonexistent.md"
        repaired = repair_frontmatter_file(record)
        assert repaired is False

    def test_self_check_uses_ruamel_yaml_strictness(self, tmp_path: Path) -> None:
        """Regression for the fix parser harmonization: the self-check
        must use the same parser as the downstream validator (ruamel.yaml).

        Verify by reading the source: an old version of repair_frontmatter_file
        used PyYAML's yaml.safe_load for both the pre-check and the post-repair
        verification. The post-2026-05-21 fix uses _ruamel_safe_load. Without
        this harmonization, the operator sees \"Repaired YAML\" log lines
        followed by invalid_outputs failures (the document passed PyYAML but
        fails the validator).
        """

        src = inspect.getsource(yaml_repair.repair_frontmatter_file)
        # Pre-check + post-check should both go through ruamel via _ruamel_safe_load.
        assert "_ruamel_safe_load" in src, (
            "repair_frontmatter_file must use _ruamel_safe_load (not yaml.safe_load) "
            "so its parser tracks the downstream validator's strictness. See the fix."
        )
        # The bare PyYAML self-check is gone — both call sites must use ruamel.
        assert "yaml.safe_load" not in src, (
            "PyYAML's yaml.safe_load should not appear in repair_frontmatter_file — "
            "parser mismatch with downstream validator caused 2026-05-21 incident."
        )
