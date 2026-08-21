"""Contract failure primitives: structured records, representation, retry verdicts.

The behaviour these pin is described in
``docs/project/specs/active/plan-2026-08-20-contract-failure-primitives.md``.
"""

from __future__ import annotations

import datetime

import pytest
from pydantic import BaseModel
from softschema import Contract, Contracts, SchemaProfile, SchemaStatus

from metaproc.engine.retry import (
    RetryVerdict,
    classify_error,
    classify_output_failures,
    merge_on_invalid,
    requires_run_abort,
    resolve_output_failure_action,
)
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


class TestOnInvalidDeclarations:
    """A process says what its own output failing its contract costs."""

    def _failure(
        self, kind: OutputFailureKind, *, invariant: str | None = None, contract: str | None = None
    ) -> OutputFailure:
        return OutputFailure(
            output="o",
            path="artifact.md",
            contract=contract,
            kind=kind,
            invariant=invariant,
            message="refused",
        )

    def test_a_stochastic_producer_can_ask_for_another_attempt(self):
        """The case that blocks declaring contracts on agent steps.

        A model that mis-extracted once may extract correctly on a retry, which
        the framework cannot know and the process can say.
        """
        failure = self._failure(OutputFailureKind.semantic, invariant="value_error")

        assert classify_output_failures([failure]) is RetryVerdict.FAIL
        assert classify_output_failures([failure], {"semantic": "retry"}) is RetryVerdict.RETRY

    def test_the_most_specific_key_wins(self):
        failure = self._failure(
            OutputFailureKind.semantic, invariant="value_error", contract="example:Thing/v1"
        )

        # invariant beats contract beats kind
        assert (
            resolve_output_failure_action(
                failure,
                {"value_error": "retry", "example:Thing/v1": "fail", "semantic": "fail_run"},
            )
            == "retry"
        )
        assert (
            resolve_output_failure_action(
                failure, {"example:Thing/v1": "fail", "semantic": "fail_run"}
            )
            == "fail"
        )
        assert resolve_output_failure_action(failure, {"semantic": "fail_run"}) == "fail_run"

    def test_saying_nothing_leaves_the_default_in_force(self):
        for kind in OutputFailureKind:
            failure = self._failure(kind)
            assert resolve_output_failure_action(failure, None) is None
            assert resolve_output_failure_action(failure, {}) is None

    def test_a_declaration_can_make_a_retryable_failure_permanent(self):
        missing = self._failure(OutputFailureKind.missing)

        assert classify_output_failures([missing]) is RetryVerdict.RETRY
        assert classify_output_failures([missing], {"missing": "fail"}) is RetryVerdict.FAIL

    def test_fail_run_is_reported_separately_from_the_retry_verdict(self):
        failure = self._failure(OutputFailureKind.structural, invariant="type")

        assert not requires_run_abort([failure])
        assert requires_run_abort([failure], {"type": "fail_run"})
        assert classify_output_failures([failure], {"type": "fail_run"}) is RetryVerdict.FAIL

    def test_one_undeclared_permanent_failure_still_sinks_the_set(self):
        retryable = self._failure(OutputFailureKind.semantic, invariant="value_error")
        permanent = self._failure(OutputFailureKind.structural, invariant="type")

        assert (
            classify_output_failures([retryable, permanent], {"value_error": "retry"})
            is RetryVerdict.FAIL
        )

    def test_outputs_merge_to_the_more_severe_action(self):
        outputs = {
            "a": IOSpec(path="a.md", on_invalid={"semantic": "retry"}),
            "b": IOSpec(path="b.md", on_invalid={"semantic": "fail_run"}),
        }

        assert merge_on_invalid(outputs) == {"semantic": "fail_run"}

    def test_a_step_declaring_nothing_merges_to_nothing(self):
        assert merge_on_invalid({"a": IOSpec(path="a.md")}) == {}


class TestAContractMeansTheSameOnEveryFormat:
    """A declaration that silently checks nothing is worse than no declaration."""

    class Score(BaseModel):
        ticker: str
        value: int

    def _registry(self) -> Contracts:
        registry = Contracts()
        registry.register(
            Contract(
                id="example:Score/v1",
                model=self.Score,
                status=SchemaStatus.enforced,
                profile=SchemaProfile.pure_yaml,
            )
        )
        return registry

    def _yaml(self, tmp_path, body: str, contract: str) -> list[OutputFailure]:
        (tmp_path / "score.yaml").write_text(body)
        return validate_item_outputs_detailed(
            tmp_path,
            {"score": IOSpec(path="score.yaml", kind="file", contract=contract)},
            softschema_registry=self._registry(),
        )

    def test_a_yaml_output_is_checked_against_its_contract(self, tmp_path):
        failures = self._yaml(tmp_path, "ticker: AAPL\nvalue: nope\n", "example:Score/v1")

        assert failures
        assert failures[0].contract == "example:Score/v1"

    def test_a_valid_yaml_output_passes(self, tmp_path):
        assert self._yaml(tmp_path, "ticker: AAPL\nvalue: 3\n", "example:Score/v1") == []

    def test_an_unresolvable_contract_fails_rather_than_passing_silently(self, tmp_path):
        """The defect this replaces: only frontmatter-md caught this."""
        failures = self._yaml(tmp_path, "ticker: AAPL\nvalue: 3\n", "example:Nope/v1")

        assert failures, "a declaration naming a contract nothing registers must fail"

    def test_an_unparsable_yaml_output_reports_unreadable(self, tmp_path):
        failures = self._yaml(tmp_path, "ticker: [unclosed\n", "example:Score/v1")

        assert failures
        assert failures[0].kind is OutputFailureKind.unreadable

    def test_a_yaml_date_normalizes_like_a_frontmatter_one(self, tmp_path):
        """The representation fix reaches every format, not just frontmatter."""

        class Dated(BaseModel):
            as_of_date: datetime.date

        registry = Contracts()
        registry.register(
            Contract(
                id="example:YDated/v1",
                model=Dated,
                status=SchemaStatus.enforced,
                profile=SchemaProfile.pure_yaml,
            )
        )
        (tmp_path / "d.yaml").write_text("as_of_date: 2026-08-21\n")

        assert (
            validate_item_outputs_detailed(
                tmp_path,
                {"d": IOSpec(path="d.yaml", kind="file", contract="example:YDated/v1")},
                softschema_registry=registry,
            )
            == []
        )
