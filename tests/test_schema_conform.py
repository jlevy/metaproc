"""What the conform pass will and will not do to an agent's document.

The refusals matter more than the fixes: a coercion that cannot be switched off
is its own bug. Each refusal below is a case the contract's own model already
distinguishes, which is why the pass does not need its own opinion about types.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Any, Literal

import pytest
from frontmatter_format import new_yaml
from pydantic import BaseModel, ConfigDict

from metaproc.engine.schema_conform import conform_frontmatter_to_model


class Named(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str


class Brand(BaseModel):
    name: str
    former_names: list[str] = []
    launch_date: str | None = None


class Profile(BaseModel):
    model_config = ConfigDict(extra="allow")
    brands: list[Brand] = []
    share: float = 0.0
    kind: Literal["core", "growth"] = "core"
    identifier: str | int = "x"
    optional_name: str | None = None


def _artifact(tmp_path: Path, frontmatter: str, body: str = "\n# Title\n\nProse.\n") -> Path:
    path = tmp_path / "artifact.md"
    path.write_text(f"---\n{frontmatter}---\n{body}")
    return path


def _reload(path: Path) -> dict[str, Any]:
    """Read the frontmatter back the way a downstream consumer would."""
    text = path.read_text()
    end = text.index("\n---\n", 4)
    return new_yaml(typ="safe").load(StringIO(text[4 : end + 1]))


class TestWhatItFixes:
    def test_a_numeric_looking_name_becomes_a_string(self, tmp_path: Path) -> None:
        path = _artifact(tmp_path, "name: 1850\n")
        assert conform_frontmatter_to_model(path, Named) is True
        restored = _reload(path)["name"]
        assert restored == "1850"
        assert isinstance(restored, str)

    @pytest.mark.parametrize(
        "written",
        ["1.10", "007", "1e3", "0x1F", ".5", "2026-01-31", "true"],
        ids=["trailing-zero", "leading-zeros", "exponent", "hex", "leading-dot", "date", "bool"],
    )
    def test_the_original_notation_survives(self, tmp_path: Path, written: str) -> None:
        """Lossless: the string is the text the author wrote, not ``str()`` of
        the parsed value, which would collapse every one of these."""
        path = _artifact(tmp_path, f"name: {written}\n")
        assert conform_frontmatter_to_model(path, Named) is True
        restored = _reload(path)["name"]
        assert isinstance(restored, str)
        assert restored == written

    def test_it_reaches_nested_list_items(self, tmp_path: Path) -> None:
        path = _artifact(tmp_path, "brands:\n    - name: Folgers\n    - name: 1850\n")
        assert conform_frontmatter_to_model(path, Profile) is True
        names = [b["name"] for b in _reload(path)["brands"]]
        assert names == ["Folgers", "1850"]
        assert all(isinstance(n, str) for n in names)

    def test_it_edits_only_the_offending_line(self, tmp_path: Path) -> None:
        """A one-character fix must not restyle the document around it."""
        # Nested under an envelope, as every real artifact is: the emitter is
        # configured to the indentation the artifact templates produce.
        frontmatter = (
            "softschema:\n"
            "  envelope: payload\n"
            "payload:\n"
            "  # reviewed by hand\n"
            "  brands:\n"
            "    - name: Folgers\n"
            "      former_names: []\n"
            "      launch_date: null\n"
            "    - name: 1850\n"
            "      former_names: []\n"
            "      launch_date: null\n"
            "  share: 1.10\n"
        )
        path = _artifact(tmp_path, frontmatter)
        before = path.read_text().splitlines()
        body_before = path.read_text().split("\n---\n", 1)[1]

        assert conform_frontmatter_to_model(path, Profile) is True

        after = path.read_text().splitlines()
        differing = [i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b]
        assert len(differing) == 1, [(before[i], after[i]) for i in differing]
        assert after[differing[0]].strip() == "- name: '1850'"
        assert "# reviewed by hand" in path.read_text(), "comments survive"
        assert "share: 1.10" in path.read_text(), "an untouched float keeps its notation"
        payload = _reload(path)["payload"]
        assert payload["brands"][0]["launch_date"] is None, "explicit null stays null"
        assert path.read_text().split("\n---\n", 1)[1] == body_before, "the body is untouched"

    def test_it_finds_the_payload_under_the_declared_envelope(self, tmp_path: Path) -> None:
        path = _artifact(tmp_path, "softschema:\n  envelope: payload\npayload:\n  name: 1850\n")
        assert conform_frontmatter_to_model(path, Named) is True
        assert _reload(path)["payload"]["name"] == "1850"

    def test_the_pass_is_idempotent(self, tmp_path: Path) -> None:
        path = _artifact(tmp_path, "name: 1850\n")
        assert conform_frontmatter_to_model(path, Named) is True
        settled = path.read_text()
        assert conform_frontmatter_to_model(path, Named) is False
        assert path.read_text() == settled


class TestWhatItRefuses:
    """Each of these is a case the model itself reports as something other than
    ``string_type``, so the pass never has to recognize it separately."""

    def test_it_never_makes_a_string_into_a_number(self, tmp_path: Path) -> None:
        path = _artifact(tmp_path, "share: not-a-number\n")
        assert conform_frontmatter_to_model(path, Profile) is False

    def test_a_union_that_accepts_the_type_is_left_alone(self, tmp_path: Path) -> None:
        """``str | int`` deliberately offers both, so 1850 is a choice."""
        path = _artifact(tmp_path, "identifier: 1850\n")
        assert conform_frontmatter_to_model(path, Profile) is False
        assert _reload(path)["identifier"] == 1850

    def test_null_is_absence_not_notation(self, tmp_path: Path) -> None:
        path = _artifact(tmp_path, "optional_name: null\n")
        assert conform_frontmatter_to_model(path, Profile) is False
        assert _reload(path)["optional_name"] is None

    def test_a_bare_null_where_a_string_is_required_is_not_invented(self, tmp_path: Path) -> None:
        """pydantic calls this ``string_type`` too, and stringifying it would
        write the word "None" into the artifact."""
        path = _artifact(tmp_path, "name: null\n")
        assert conform_frontmatter_to_model(path, Named) is False
        assert _reload(path)["name"] is None

    def test_a_shape_mismatch_is_left_to_fail(self, tmp_path: Path) -> None:
        path = _artifact(tmp_path, "name:\n  first: Jane\n")
        assert conform_frontmatter_to_model(path, Named) is False

    def test_a_bad_enum_value_is_left_to_fail(self, tmp_path: Path) -> None:
        path = _artifact(tmp_path, "kind: sunset\n")
        assert conform_frontmatter_to_model(path, Profile) is False

    def test_a_field_the_model_does_not_describe_is_untouched(self, tmp_path: Path) -> None:
        path = _artifact(tmp_path, "name: Folgers\nunknown_extra: 1850\n")
        assert conform_frontmatter_to_model(path, Named) is False
        assert _reload(path)["unknown_extra"] == 1850

    def test_an_unparseable_document_is_left_for_yaml_repair(self, tmp_path: Path) -> None:
        path = _artifact(tmp_path, "name: a: b: c\n")
        assert conform_frontmatter_to_model(path, Named) is False

    def test_a_file_without_frontmatter_is_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "plain.md"
        path.write_text("# Just a heading\n")
        assert conform_frontmatter_to_model(path, Named) is False

    def test_a_clean_document_is_not_rewritten(self, tmp_path: Path) -> None:
        path = _artifact(tmp_path, "name: Folgers\n")
        before = path.read_text()
        assert conform_frontmatter_to_model(path, Named) is False
        assert path.read_text() == before
