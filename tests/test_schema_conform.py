"""What the conform pass will and will not do to an agent's document."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from metaproc.engine.schema_conform import (
    conform_frontmatter_to_schema,
    conform_outputs_to_contracts,
)

STRING_NAME = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
}


def _artifact(tmp_path: Path, frontmatter: str, body: str = "\n# Title\n") -> Path:
    path = tmp_path / "artifact.md"
    path.write_text(f"---\n{frontmatter}---\n{body}")
    return path


class TestWhatItFixes:
    def test_a_numeric_looking_name_becomes_a_string(self, tmp_path: Path) -> None:
        path = _artifact(tmp_path, "name: 1850\n")
        assert conform_frontmatter_to_schema(path, STRING_NAME) is True
        assert "name: '1850'" in path.read_text()

    @pytest.mark.parametrize(
        "written",
        ["1.10", "007", "1e3", "0x1F", ".5", "2026-01-31"],
        ids=["trailing-zero", "leading-zeros", "exponent", "hex", "leading-dot", "date"],
    )
    def test_the_original_notation_survives(self, tmp_path: Path, written: str) -> None:
        """`str()` of the parsed value would collapse every one of these."""
        path = _artifact(tmp_path, f"name: {written}\n")
        assert conform_frontmatter_to_schema(path, STRING_NAME) is True
        assert f"name: '{written}'" in path.read_text()

    def test_it_reaches_through_refs_and_arrays(self, tmp_path: Path) -> None:
        schema = {
            "$defs": {"Brand": STRING_NAME},
            "type": "object",
            "properties": {"brands": {"type": "array", "items": {"$ref": "#/$defs/Brand"}}},
        }
        path = _artifact(tmp_path, "brands:\n  - name: Folgers\n  - name: 1850\n")
        assert conform_frontmatter_to_schema(path, schema) is True
        text = path.read_text()
        assert "- name: Folgers" in text, "an untouched sibling must not be requoted"
        assert "- name: '1850'" in text

    def test_it_edits_only_the_offending_line(self, tmp_path: Path) -> None:
        """A one-character fix must not reformat the document around it."""
        frontmatter = (
            "brands:\n"
            "    - name: Folgers\n"
            "      launch_date: null\n"
            "    - name: 1850\n"
            "      launch_date: null\n"
        )
        path = _artifact(tmp_path, frontmatter)
        before = path.read_text().splitlines()
        assert (
            conform_frontmatter_to_schema(
                path,
                {
                    "type": "object",
                    "properties": {"brands": {"type": "array", "items": STRING_NAME}},
                },
            )
            is True
        )
        after = path.read_text().splitlines()
        differing = [i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b]
        assert len(differing) == 1, f"expected 1 changed line, got {differing}"
        assert "launch_date: null" in path.read_text(), "null must not collapse to empty"

    def test_it_finds_the_payload_under_the_declared_envelope(self, tmp_path: Path) -> None:
        path = _artifact(
            tmp_path,
            "softschema:\n  envelope: company_profile\ncompany_profile:\n  name: 1850\n",
        )
        assert conform_frontmatter_to_schema(path, STRING_NAME) is True
        assert "name: '1850'" in path.read_text()


class TestWhatItRefuses:
    """The boundary: a notation accident is fixed, a real disagreement is not."""

    def test_it_never_makes_a_string_into_a_number(self, tmp_path: Path) -> None:
        schema = {"type": "object", "properties": {"count": {"type": "number"}}}
        path = _artifact(tmp_path, "count: not-a-number\n")
        assert conform_frontmatter_to_schema(path, schema) is False

    def test_a_schema_that_accepts_the_type_is_left_alone(self, tmp_path: Path) -> None:
        """`str | int` deliberately offers both, so 1850 is a choice, not an accident."""
        schema = {
            "type": "object",
            "properties": {"id": {"anyOf": [{"type": "string"}, {"type": "integer"}]}},
        }
        path = _artifact(tmp_path, "id: 1850\n")
        assert conform_frontmatter_to_schema(path, schema) is False

    def test_null_is_absence_not_notation(self, tmp_path: Path) -> None:
        schema = {
            "type": "object",
            "properties": {"name": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
        }
        path = _artifact(tmp_path, "name: null\n")
        assert conform_frontmatter_to_schema(path, schema) is False
        assert "name: null" in path.read_text()

    def test_a_shape_mismatch_is_left_to_fail(self, tmp_path: Path) -> None:
        """An object where a string belongs is a real defect, not a quoting one."""
        path = _artifact(tmp_path, "name:\n  first: Jane\n")
        assert conform_frontmatter_to_schema(path, STRING_NAME) is False

    def test_a_field_the_schema_does_not_describe_is_untouched(self, tmp_path: Path) -> None:
        path = _artifact(tmp_path, "name: Folgers\nunknown_extra: 1850\n")
        assert conform_frontmatter_to_schema(path, STRING_NAME) is False

    def test_an_unparseable_document_is_left_for_yaml_repair(self, tmp_path: Path) -> None:
        path = _artifact(tmp_path, "name: a: b: c\n")
        assert conform_frontmatter_to_schema(path, STRING_NAME) is False

    def test_a_file_without_frontmatter_is_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "plain.md"
        path.write_text("# Just a heading\n")
        assert conform_frontmatter_to_schema(path, STRING_NAME) is False

    def test_a_clean_document_is_not_rewritten(self, tmp_path: Path) -> None:
        path = _artifact(tmp_path, "name: Folgers\n")
        before = path.read_text()
        assert conform_frontmatter_to_schema(path, STRING_NAME) is False
        assert path.read_text() == before


class _FakeContract:
    """A registry entry carrying just the model surface conform reads."""

    envelope_key = None

    class model:  # noqa: N801 -- stands in for a pydantic model class
        @staticmethod
        def model_json_schema() -> dict[str, Any]:
            return STRING_NAME


class _FakeRegistry:
    def resolve(self, _contract_id: str) -> _FakeContract:
        return _FakeContract()


class _Spec:
    """The IOSpec surface conform reads, without building a real plan."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.contract = "example:Thing/v1"
        self.format = "frontmatter-md"


class TestOutputResolution:
    """Conform must resolve an output the way validation resolves it.

    Resolving by basename under the artifact directory silently skips an output
    whose basename carries a runtime var, and probes the wrong place for one
    living outside that directory. Both are the defects the repair pass was
    fixed for; conform inherits the fix rather than the bug.
    """

    def test_a_templated_basename_is_rendered_before_the_file_is_found(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "ACME-brief.md"
        target.write_text("---\nname: 1850\n---\n\n# Brief\n")
        changed = conform_outputs_to_contracts(
            tmp_path,
            {"out": _Spec("{{ticker}}-brief.md")},
            variables={"ticker": "ACME"},
            registry=_FakeRegistry(),
        )
        assert changed == ["ACME-brief.md"]
        assert "name: '1850'" in target.read_text()

    def test_an_output_outside_the_artifact_dir_is_conformed_where_it_lives(
        self, tmp_path: Path
    ) -> None:
        elsewhere = tmp_path / "published"
        elsewhere.mkdir()
        target = elsewhere / "report.md"
        target.write_text("---\nname: 1850\n---\n\n# Report\n")
        changed = conform_outputs_to_contracts(
            tmp_path / "items" / "ACME",
            {"out": _Spec(str(target))},
            registry=_FakeRegistry(),
        )
        assert changed == ["report.md"]
        assert "name: '1850'" in target.read_text()

    def test_a_single_name_still_resolves_under_the_artifact_dir(self, tmp_path: Path) -> None:
        target = tmp_path / "brief.md"
        target.write_text("---\nname: 1850\n---\n\n# Brief\n")
        changed = conform_outputs_to_contracts(
            tmp_path, {"out": _Spec("brief.md")}, registry=_FakeRegistry()
        )
        assert changed == ["brief.md"]
