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
from pydantic import BaseModel, ConfigDict, field_validator

from metaproc.engine.schema_conform import conform_declared_outputs, conform_frontmatter_to_model
from metaproc.models.authored import IOSpec
from metaproc.plugins.discovery import get_plugin_registry


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


class TestShapesTheModelDescribes:
    """Coverage that came free with borrowing pydantic's verdict.

    A hand-rolled JSON Schema walker has to know that ``dict[str, X]`` is spelled
    ``additionalProperties`` and a tuple ``prefixItems``, and silently covers
    nothing where it does not. Asking the model removes the question — these pin
    that it stays removed.
    """

    def test_a_dict_of_strings_is_reached(self, tmp_path: Path) -> None:
        class Meta(BaseModel):
            meta: dict[str, str] = {}

        path = _artifact(tmp_path, "meta:\n  founded: 1850\n  city: Portland\n")
        assert conform_frontmatter_to_model(path, Meta) is True
        assert _reload(path)["meta"] == {"founded": "1850", "city": "Portland"}

    def test_a_tuple_is_reached_per_position(self, tmp_path: Path) -> None:
        class Pair(BaseModel):
            pair: tuple[str, int]

        path = _artifact(tmp_path, "pair:\n  - 1850\n  - 12\n")
        assert conform_frontmatter_to_model(path, Pair) is True
        assert _reload(path)["pair"] == ["1850", 12], "position 0 only"

    def test_an_optional_string_is_reached_through_its_union(self, tmp_path: Path) -> None:
        """``str | None`` is the shape a JSON Schema walker most often misreads,
        and the one the artifact templates use most."""
        path = _artifact(tmp_path, "brands:\n  - name: ok\n    launch_date: 2026\n")
        assert conform_frontmatter_to_model(path, Profile) is True
        assert _reload(path)["brands"][0]["launch_date"] == "2026"

    def test_a_model_behind_an_optional_is_reached(self, tmp_path: Path) -> None:
        class Wrapper(BaseModel):
            inner: Brand | None = None

        path = _artifact(tmp_path, "inner:\n  name: 1850\n")
        assert conform_frontmatter_to_model(path, Wrapper) is True
        assert _reload(path)["inner"]["name"] == "1850"


class TestEnvelopeResolution:
    """Which level of the document the model is understood to describe."""

    def test_a_named_envelope_that_is_absent_is_not_silently_the_root(self, tmp_path: Path) -> None:
        """The bug this guards: an agent omits the envelope, and the envelope's
        model gets applied to the frontmatter root, which describes other fields.
        Validation reports the missing envelope; conform must not rewrite the
        document on the way there."""
        path = _artifact(tmp_path, "name: 1850\n")
        before = path.read_text()
        assert conform_frontmatter_to_model(path, Named, envelope_key="profile") is False
        assert path.read_text() == before
        assert _reload(path)["name"] == 1850

    def test_a_named_envelope_that_is_present_is_used(self, tmp_path: Path) -> None:
        path = _artifact(tmp_path, "profile:\n  name: 1850\n")
        assert conform_frontmatter_to_model(path, Named, envelope_key="profile") is True
        assert _reload(path)["profile"]["name"] == "1850"

    def test_an_explicit_none_means_the_root_and_ignores_the_document(self, tmp_path: Path) -> None:
        """A contract with no envelope key validates the frontmatter root, so
        conform must read the root too — even when the document names an
        envelope of its own."""
        path = _artifact(tmp_path, "softschema:\n  envelope: profile\nname: 1850\n")
        assert conform_frontmatter_to_model(path, Named, envelope_key=None) is True
        assert _reload(path)["name"] == "1850"

    def test_the_default_reads_the_envelope_from_the_document(self, tmp_path: Path) -> None:
        """FROM_DOCUMENT is the standalone caller's path: no contract in hand, so
        the artifact's own softschema block names the payload."""
        path = _artifact(tmp_path, "softschema:\n  envelope: profile\nprofile:\n  name: 1850\n")
        assert conform_frontmatter_to_model(path, Named) is True
        assert _reload(path)["profile"]["name"] == "1850"


class TestRecordedNotationLimits:
    """Two notations the round-trip value does not carry.

    Recorded rather than fixed: recovering either means re-reading the source
    text by hand, which is the string surgery this module exists to avoid.
    Neither is reachable for a name a person would give something.
    """

    @pytest.mark.parametrize("written", ["True", "TRUE"], ids=["capitalized", "upper"])
    def test_a_boolean_comes_back_canonically_spelled(self, tmp_path: Path, written: str) -> None:
        path = _artifact(tmp_path, f"name: {written}\n")
        assert conform_frontmatter_to_model(path, Named) is True
        assert _reload(path)["name"] == "true"

    def test_an_integers_leading_plus_is_dropped(self, tmp_path: Path) -> None:
        path = _artifact(tmp_path, "name: +1_000\n")
        assert conform_frontmatter_to_model(path, Named) is True
        assert _reload(path)["name"] == "1_000", "the underscore survives, the sign does not"


class TestTheFileItself:
    """Properties of the re-emitted file, independent of any model."""

    def test_an_anchor_and_its_alias_survive(self, tmp_path: Path) -> None:
        """``new_yaml`` disables aliases by default, which expands ``*base`` into
        a duplicated mapping — a structural rewrite of a document this pass is
        supposed to touch on one line."""

        class Two(BaseModel):
            a: Named
            b: Named

        path = _artifact(tmp_path, "a: &base\n  name: 1850\nb: *base\n")
        assert conform_frontmatter_to_model(path, Two) is True
        text = path.read_text()
        assert "&base" in text and "*base" in text, f"anchor and alias must survive:\n{text}"

    def test_an_explicit_null_keeps_its_spelling(self, tmp_path: Path) -> None:
        """Not incidental: ruamel's round-trip representer spells a null empty
        once anything has been represented, so enabling aliases moves it."""
        path = _artifact(tmp_path, "brands:\n  - name: 1850\n    launch_date: null\n")
        assert conform_frontmatter_to_model(path, Profile) is True
        assert "launch_date: null" in path.read_text()

    def test_crlf_line_endings_are_not_reflowed(self, tmp_path: Path) -> None:
        path = tmp_path / "crlf.md"
        path.write_bytes(b"---\r\nname: 1850\r\n---\r\n\r\n# Title\r\n")
        assert conform_frontmatter_to_model(path, Named) is True
        raw = path.read_bytes()
        assert raw == b"---\r\nname: '1850'\r\n---\r\n\r\n# Title\r\n"
        assert b"\n" not in raw.replace(b"\r\n", b""), "no bare LF introduced"

    def test_an_html_comment_frontmatter_document_is_out_of_scope(self, tmp_path: Path) -> None:
        path = tmp_path / "html.md"
        path.write_text("<!---\nname: 1850\n--->\n\n# Title\n")
        before = path.read_text()
        assert conform_frontmatter_to_model(path, Named) is False
        assert path.read_text() == before


class TestAModelThatWillNotRun:
    """The pass is an optimization on the way to validation, which runs next.

    A contract model is downstream code and its validators can raise anything.
    Turning the pass off for that artifact is right; taking the agent step down
    with it is not.
    """

    def test_a_validator_raising_a_non_validation_error_does_not_escape(
        self, tmp_path: Path
    ) -> None:
        class Broken(BaseModel):
            name: str

            @field_validator("name", mode="before")
            @classmethod
            def _boom(cls, _value: object) -> object:
                raise KeyError("an upstream lookup this pass has no business triggering")

        path = _artifact(tmp_path, "name: 1850\n")
        before = path.read_text()
        assert conform_frontmatter_to_model(path, Broken) is False
        assert path.read_text() == before


class TestDeclaredOutputs:
    """The registry-driven entry point, with a real contract and IOSpec."""

    @staticmethod
    def _outputs(fmt: str = "frontmatter-md", path: str = "qa.md") -> dict[str, IOSpec]:
        return {"report": IOSpec(path=path, contract="metaproc:QaReport/0.1", format=fmt)}

    def test_it_conforms_a_declared_output_under_its_contract_envelope(
        self, tmp_path: Path
    ) -> None:
        registry = get_plugin_registry().softschemas
        path = tmp_path / "qa.md"
        path.write_text("---\nqa:\n  item_id: 1850\n---\n\n# QA\n")
        changed = conform_declared_outputs(
            tmp_path, self._outputs(), variables={}, registry=registry
        )
        assert changed == [path]
        assert "item_id: '1850'" in path.read_text()

    def test_a_missing_contract_envelope_leaves_the_root_untouched(self, tmp_path: Path) -> None:
        """The end-to-end shape of the envelope bug: the agent omitted ``qa:``,
        so ``item_id`` at the root is not the ``item_id`` the model describes."""
        registry = get_plugin_registry().softschemas
        path = tmp_path / "qa.md"
        path.write_text("---\nitem_id: 1850\n---\n\n# QA\n")
        before = path.read_text()
        assert (
            conform_declared_outputs(tmp_path, self._outputs(), variables={}, registry=registry)
            == []
        )
        assert path.read_text() == before

    def test_a_templated_output_path_resolves_the_way_validation_resolves_it(
        self, tmp_path: Path
    ) -> None:
        registry = get_plugin_registry().softschemas
        item_dir = tmp_path / "acme"
        item_dir.mkdir()
        (item_dir / "acme-qa.md").write_text("---\nqa:\n  item_id: 1850\n---\n\n# QA\n")
        changed = conform_declared_outputs(
            item_dir,
            self._outputs(path="{{item}}-qa.md"),
            variables={"item": "acme"},
            registry=registry,
        )
        assert changed == [item_dir / "acme-qa.md"]

    def test_a_non_frontmatter_format_is_not_conformed(self, tmp_path: Path) -> None:
        """Anything else is written by a real serializer that already quotes."""
        registry = get_plugin_registry().softschemas
        path = tmp_path / "qa.md"
        path.write_text("---\nqa:\n  item_id: 1850\n---\n\n# QA\n")
        before = path.read_text()
        assert (
            conform_declared_outputs(
                tmp_path, self._outputs(fmt="yaml"), variables={}, registry=registry
            )
            == []
        )
        assert path.read_text() == before
