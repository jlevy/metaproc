"""Contract failure primitives: structured records, representation, retry verdicts.

The behaviour these pin is described in
``docs/project/specs/active/plan-2026-08-20-contract-failure-primitives.md``.
"""

from __future__ import annotations

import datetime

import pytest
from pydantic import BaseModel
from softschema import Contract, Contracts, SchemaStatus

from metaproc.engine.retry import RetryVerdict, classify_error, classify_output_failures
from metaproc.engine.validation import (
    normalize_for_structural_pass,
    validate_item_outputs,
    validate_item_outputs_detailed,
)
from metaproc.models.authored import IOSpec
from metaproc.models.runtime import OutputFailure, OutputFailureKind


class Dated(BaseModel):
    as_of_date: datetime.date
    name: str


def dated_registry() -> Contracts:
    registry = Contracts()
    registry.register(
        Contract(
            id="example:Dated/v1",
            model=Dated,
            envelope_key="dated",
            status=SchemaStatus.enforced,
        )
    )
    return registry


def dated_outputs() -> dict[str, IOSpec]:
    return {"main": IOSpec(path="output.md", format="frontmatter-md", contract="example:Dated/v1")}


class TestRepresentationNormalization:
    """A document and the schema describing it should agree about what a date is."""

    @pytest.mark.parametrize(
        ("label", "literal"),
        [("quoted", '"2026-08-21"'), ("unquoted", "2026-08-21")],
    )
    def test_a_date_validates_however_yaml_spelled_it(self, tmp_path, label, literal):
        (tmp_path / "output.md").write_text(
            f"---\ndated:\n  as_of_date: {literal}\n  name: x\n---\nBody\n"
        )

        failures = validate_item_outputs_detailed(
            tmp_path, dated_outputs(), softschema_registry=dated_registry()
        )

        assert failures == [], f"{label} date should validate: {[f.message for f in failures]}"

    def test_a_genuine_type_error_still_fails(self, tmp_path):
        (tmp_path / "output.md").write_text(
            "---\ndated:\n  as_of_date: not-a-date\n  name: x\n---\nBody\n"
        )

        failures = validate_item_outputs_detailed(
            tmp_path, dated_outputs(), softschema_registry=dated_registry()
        )

        assert failures, "an unparsable date must still be refused"

    def test_normalization_leaves_other_values_alone(self):
        payload = {
            "n": 1,
            "s": "x",
            "b": True,
            "none": None,
            "nested": [{"d": datetime.date(2026, 8, 21)}],
        }

        assert normalize_for_structural_pass(payload) == {
            "n": 1,
            "s": "x",
            "b": True,
            "none": None,
            "nested": [{"d": "2026-08-21"}],
        }


class TestStructuredFailures:
    def test_a_missing_file_reports_its_output_and_kind(self, tmp_path):
        outputs = {"report": IOSpec(path="missing.md")}

        (failure,) = validate_item_outputs_detailed(tmp_path, outputs)

        assert failure.output == "report"
        assert failure.kind is OutputFailureKind.missing
        assert failure.path == "missing.md"

    def test_an_empty_directory_is_distinguishable_from_a_missing_one(self, tmp_path):
        (tmp_path / "present").mkdir()
        outputs = {
            "there": IOSpec(path="present", kind="directory"),
            "gone": IOSpec(path="absent", kind="directory"),
        }

        by_output = {f.output: f.kind for f in validate_item_outputs_detailed(tmp_path, outputs)}

        assert by_output == {
            "there": OutputFailureKind.empty,
            "gone": OutputFailureKind.missing,
        }

    def test_a_refused_document_names_the_invariant_and_location(self, tmp_path):
        (tmp_path / "output.md").write_text("---\ndated:\n  as_of_date: 2026-08-21\n---\nBody\n")

        failures = validate_item_outputs_detailed(
            tmp_path, dated_outputs(), softschema_registry=dated_registry()
        )

        assert failures
        assert any(f.invariant for f in failures), "the refusing validator should be named"
        assert all(f.contract == "example:Dated/v1" for f in failures)

    def test_every_refusing_invariant_is_kept(self, tmp_path):
        """The string view historically reported only the first."""

        class Strict(BaseModel):
            a: str
            b: str
            c: str

        registry = Contracts()
        registry.register(
            Contract(
                id="example:Strict/v1",
                model=Strict,
                envelope_key="strict",
                status=SchemaStatus.enforced,
            )
        )
        (tmp_path / "output.md").write_text("---\nstrict: {}\n---\nBody\n")
        outputs = {
            "main": IOSpec(path="output.md", format="frontmatter-md", contract="example:Strict/v1")
        }

        failures = validate_item_outputs_detailed(tmp_path, outputs, softschema_registry=registry)

        assert len(failures) > 1, "three missing required fields should not collapse to one"


class TestStringViewIsUnchanged:
    """Every existing caller reads strings; those must not move."""

    def test_the_string_view_is_the_summary_of_each_failure(self, tmp_path):
        (tmp_path / "present").mkdir()
        outputs = {
            "a": IOSpec(path="missing.md"),
            "b": IOSpec(path="present", kind="directory"),
        }

        errors = validate_item_outputs(tmp_path, outputs)

        assert errors == [
            "missing.md: file not found",
            "present: directory is empty (no output files produced)",
        ]


class TestRetryVerdictIgnoresFilenames:
    """The defect this replaces: the substring test read the artifact's name."""

    def _missing(self, path: str) -> OutputFailure:
        return OutputFailure(
            output="out", path=path, kind=OutputFailureKind.missing, message="file not found"
        )

    def test_the_string_path_is_filename_sensitive(self):
        """Pins the behaviour being replaced, so the improvement is visible."""
        schema_named = (
            "output validation failed: company-research-schema-manifest.md: file not found"
        )
        plain = "output validation failed: source-snapshot.md: file not found"

        assert classify_error(schema_named) is RetryVerdict.FAIL
        assert classify_error(plain) is RetryVerdict.RETRY

    def test_the_structured_path_is_not(self):
        schema_named = self._missing("company-research-schema-manifest.md")
        plain = self._missing("source-snapshot.md")

        assert classify_output_failures([schema_named]) is RetryVerdict.RETRY
        assert classify_output_failures([plain]) is RetryVerdict.RETRY

    def test_a_refused_document_is_permanent(self):
        refused = OutputFailure(
            output="out", path="x.md", kind=OutputFailureKind.structural, message="refused"
        )

        assert classify_output_failures([refused]) is RetryVerdict.FAIL

    def test_one_unfixable_failure_makes_the_set_permanent(self):
        assert (
            classify_output_failures(
                [
                    self._missing("a.md"),
                    OutputFailure(
                        output="b", path="b.md", kind=OutputFailureKind.semantic, message="refused"
                    ),
                ]
            )
            is RetryVerdict.FAIL
        )

    def test_no_failures_is_not_a_retry(self):
        assert classify_output_failures([]) is RetryVerdict.FAIL
