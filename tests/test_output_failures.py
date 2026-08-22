"""Contract failure primitives: structured records, representation, retry verdicts.

What a contract failure records and what it costs, per ``arch-metaproc-core.md``
§6 (the `contract` and `on_invalid` declarations) and §14.1 (the retry chain).
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest
from pydantic import BaseModel
from softschema import Contract, Contracts, SchemaProfile, SchemaStatus

from metaproc.commands.run_parallel import _handle_success
from metaproc.engine.build_plan import _resolve_step_inputs
from metaproc.engine.retry import (
    RetryVerdict,
    classify_error,
    classify_output_failures,
    requires_run_abort,
    resolve_output_failure_action,
)
from metaproc.engine.validation import (
    check_artifact_contract,
    normalize_for_structural_pass,
    repair_declared_outputs,
    validate_item_outputs,
    validate_item_outputs_detailed,
)
from metaproc.io.state_io import mark_running_at, read_status_at
from metaproc.models.authored import IOSpec, ProcessStep
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

    def _outputs(self, on_invalid: dict[str, str] | None) -> dict[str, IOSpec]:
        """The declaring output, named to match ``_failure``'s ``output``."""
        return {"o": IOSpec(path="artifact.md", on_invalid=on_invalid)}  # pyright: ignore[reportArgumentType]

    def test_a_stochastic_producer_can_ask_for_another_attempt(self):
        """The case that blocks declaring contracts on agent steps.

        A model that mis-extracted once may extract correctly on a retry, which
        the framework cannot know and the process can say.
        """
        failure = self._failure(OutputFailureKind.semantic, invariant="value_error")

        assert classify_output_failures([failure]) is RetryVerdict.FAIL
        assert (
            classify_output_failures([failure], self._outputs({"semantic": "retry"}))
            is RetryVerdict.RETRY
        )

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
        assert (
            classify_output_failures([missing], self._outputs({"missing": "fail"}))
            is RetryVerdict.FAIL
        )

    def test_fail_run_is_reported_separately_from_the_retry_verdict(self):
        failure = self._failure(OutputFailureKind.structural, invariant="type")
        outputs = self._outputs({"type": "fail_run"})

        assert not requires_run_abort([failure])
        assert requires_run_abort([failure], outputs)
        assert classify_output_failures([failure], outputs) is RetryVerdict.FAIL

    def test_one_undeclared_permanent_failure_still_sinks_the_set(self):
        retryable = self._failure(OutputFailureKind.semantic, invariant="value_error")
        permanent = self._failure(OutputFailureKind.structural, invariant="type")

        assert (
            classify_output_failures(
                [retryable, permanent], self._outputs({"value_error": "retry"})
            )
            is RetryVerdict.FAIL
        )

    def test_a_declaration_governs_only_the_output_that_made_it(self):
        """Two outputs of one step have two contracts and two reasons to fail."""
        outputs = {
            "lenient": IOSpec(path="a.md", on_invalid={"semantic": "retry"}),
            "strict": IOSpec(path="b.md"),
        }
        strict_failure = OutputFailure(
            output="strict", path="b.md", kind=OutputFailureKind.semantic, message="refused"
        )
        lenient_failure = OutputFailure(
            output="lenient", path="a.md", kind=OutputFailureKind.semantic, message="refused"
        )

        assert classify_output_failures([lenient_failure], outputs) is RetryVerdict.RETRY
        assert classify_output_failures([strict_failure], outputs) is RetryVerdict.FAIL

    def test_a_failure_from_an_undeclared_output_falls_back_to_the_default(self):
        outputs = {"declared": IOSpec(path="a.md", on_invalid={"missing": "fail"})}
        stray = OutputFailure(
            output="not_in_the_spec",
            path="z.md",
            kind=OutputFailureKind.missing,
            message="file not found",
        )

        assert classify_output_failures([stray], outputs) is RetryVerdict.RETRY


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


class TestTheRecordSurvivesTheAgentPool:
    """The pool is the path the filename-sensitive verdict was observed on.

    ``run_parallel`` reaches output validation twice: once inline for
    ``mode: code``, and once through the agent pool, which is where a ``for_each``
    agent step ends up. A structured record that only the first path produces
    leaves the second reading the sentence, so these pin the pool seam.
    """

    def _pool_call(self, tmp_path, output_path: str):
        state_dir = tmp_path / ".state"
        state_dir.mkdir()
        running = mark_running_at(state_dir, run_id="r1", step_id="s1", item={"co": "ACME"})
        item_dir = tmp_path / "item"
        item_dir.mkdir()

        class _Out:
            def progress(self, _message: str) -> None: ...

        class _Result:
            elapsed_s = 1.0

        outputs = {"report": IOSpec(path=output_path)}
        batch_failed: list[tuple[dict[str, object], str, list[OutputFailure]]] = []
        _handle_success(
            "co",
            "ACME",
            item_dir,
            state_dir,
            {"co": "ACME"},
            None,
            running,
            outputs,
            {},
            "r1",
            "s1",
            _Result(),
            _Out(),
            [],
            batch_failed,
            {"attempt_number": 1},
        )
        return batch_failed, read_status_at(state_dir), outputs

    def test_a_failed_pool_item_keeps_its_structured_record(self, tmp_path):
        batch_failed, status, _ = self._pool_call(tmp_path, "report.md")

        assert status is not None
        assert status.state == "failed"
        assert [f.kind for f in status.output_failures] == [OutputFailureKind.missing]
        assert status.output_failures[0].output == "report"
        assert status.error == "output validation failed: report.md: file not found"

        (_, _, failures) = batch_failed[0]
        assert [f.kind for f in failures] == [OutputFailureKind.missing]

    def test_the_pool_verdict_no_longer_reads_the_filename(self, tmp_path):
        """The end-to-end form of the defect: same miss, name containing 'schema'."""
        batch_failed, _, outputs = self._pool_call(tmp_path, "company-research-schema-manifest.md")
        (_, error_str, failures) = batch_failed[0]

        assert classify_error(error_str) is RetryVerdict.FAIL, "the sentence still misreads it"
        assert classify_output_failures(failures, outputs) is RetryVerdict.RETRY


class TestRefResolvedInputsKeepTheirContract:
    def test_an_input_bound_by_ref_inherits_the_producer_contract(self):
        producer_outputs = {
            "generate": {
                "record": IOSpec(path="record.md", format="frontmatter-md", contract="ex:Rec/v1")
            }
        }
        consumer = ProcessStep(
            id="consume",
            mode="code",
            command="true",
            inputs={"source": IOSpec(ref="generate.record")},
        )

        resolved, _ = _resolve_step_inputs(
            consumer, step_outputs=producer_outputs, params={}, resolved_deps={}
        )

        assert resolved["source"].contract == "ex:Rec/v1"
        assert resolved["source"].model_dump(by_alias=True)["contract"] == "ex:Rec/v1"


class TestAnEnvelopedContractStillAcceptsABareRoot:
    """A YAML output used to be loaded straight into its model, so its root *was* the model."""

    class Score(BaseModel):
        ticker: str
        value: int

    def _registry(self) -> Contracts:
        registry = Contracts()
        registry.register(
            Contract(
                id="example:Enveloped/v1",
                model=self.Score,
                envelope_key="score",
                status=SchemaStatus.enforced,
            )
        )
        return registry

    def _check(self, tmp_path, body: str) -> list[OutputFailure]:
        (tmp_path / "score.yaml").write_text(body)
        return validate_item_outputs_detailed(
            tmp_path,
            {"score": IOSpec(path="score.yaml", kind="file", contract="example:Enveloped/v1")},
            softschema_registry=self._registry(),
        )

    def test_a_bare_root_written_under_the_older_reading_still_validates(self, tmp_path):
        assert self._check(tmp_path, "ticker: AAPL\nvalue: 3\n") == []

    def test_an_enveloped_document_validates_too(self, tmp_path):
        assert self._check(tmp_path, "score:\n  ticker: AAPL\n  value: 3\n") == []

    def test_a_wrong_value_is_still_refused_either_way(self, tmp_path):
        assert self._check(tmp_path, "ticker: AAPL\nvalue: nope\n")
        assert self._check(tmp_path, "score:\n  ticker: AAPL\n  value: nope\n")


class TestValidateCommandAgreesWithTheStepBoundary:
    """One binary must not answer 'is this artifact valid?' two ways."""

    def test_both_read_the_same_check(self, tmp_path):
        registry = dated_registry()
        artifact = tmp_path / "output.md"
        # An unquoted YAML date: the representation defect the engine normalizes.
        artifact.write_text("---\ndated:\n  as_of_date: 2026-08-21\n  name: x\n---\nBody\n")

        engine_failures = validate_item_outputs_detailed(
            tmp_path, dated_outputs(), softschema_registry=registry
        )
        command_failures = check_artifact_contract(
            artifact,
            contract_id="example:Dated/v1",
            fmt="frontmatter-md",
            registry=registry,
            output="main",
            path="output.md",
        )

        assert engine_failures == []
        assert command_failures == []


class TestRepairDeclaredOutputs:
    """Repair probes the same file validation is about to read.

    The pre-validation repair pass resolves output paths with the validator's
    own rules — templates rendered, absolute and multi-part paths taken as-is —
    so a basename carrying a runtime var, or an output living outside the first
    output's parent directory, is still repaired, and a directory output is
    never handed to the frontmatter reader.
    """

    _BROKEN = "---\nrecord:\n  detail: Strong beat (Note: actually Q1 not Q2)\n---\nbody\n"

    def test_renders_runtime_vars_and_reaches_outputs_in_other_dirs(self, tmp_path: Path) -> None:
        first = tmp_path / "notes" / "first.md"
        first.parent.mkdir()
        first.write_text(self._BROKEN)
        second = tmp_path / "reports" / "summary-claude.md"
        second.parent.mkdir()
        second.write_text(self._BROKEN)
        outputs = {
            "first": IOSpec(path=str(first), kind="file"),
            "second": IOSpec(
                path=str(tmp_path / "reports" / "summary-{{VARIANT}}.md"), kind="file"
            ),
        }

        repaired = repair_declared_outputs(first.parent, outputs, variables={"VARIANT": "claude"})

        assert sorted(p.name for p in repaired) == ["first.md", "summary-claude.md"]
        assert '"Strong beat (Note: actually Q1 not Q2)"' in second.read_text()

    def test_directory_outputs_and_missing_files_are_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "out-dir").mkdir()
        outputs = {
            "d": IOSpec(path=str(tmp_path / "out-dir"), kind="directory"),
            "gone": IOSpec(path=str(tmp_path / "never-written.md"), kind="file"),
        }

        assert repair_declared_outputs(tmp_path, outputs, variables={}) == []
