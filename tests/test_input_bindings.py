"""The comparison a scope makes between its recorded input bindings and a launch.

``engine.input_bindings`` sorts each recorded and each declared ``on_change: new_run``
input into changed, released, or adopted, and renders the refusal a changed one earns.
The end-to-end behavior at every entry point is in ``test_resume_launch_changes.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from metaproc.engine.input_bindings import (
    InputBindingChanges,
    apply_input_binding_changes,
    compare_input_bindings,
    declared_input_bindings,
    input_binding_refusal,
    recorded_input_bindings,
)
from metaproc.io.state_io import read_input_bindings, write_input_bindings
from metaproc.models.authored import ProcessSpec
from metaproc.models.runtime import InputBinding, InputBindingsRecord
from metaproc.paths import STATE_DIR, input_bindings_file


def _spec(**inputs: dict[str, object]) -> ProcessSpec:
    return ProcessSpec.model_validate({"name": "mine", "inputs": inputs})


def _new_run(param: str, **fields: object) -> dict[str, object]:
    return {"param": param, "as": "string", "on_change": "new_run", **fields}


def _record(param: str, **fields: object) -> dict[str, object]:
    return {"param": param, "as": "string", **fields}


def _bound(value: str | None) -> InputBinding:
    return InputBinding(on_change="new_run", value=value)


BOUND = _spec(as_of=_new_run("AS_OF"), code_rev=_record("CODE_REV"))
RECORD_ONLY = _spec(as_of=_record("AS_OF"), code_rev=_record("CODE_REV"))


class TestDeclaredBindings:
    def test_reads_the_logical_name(self) -> None:
        assert declared_input_bindings(BOUND, {"as_of": "d1", "AS_OF": "d1", "code_rev": "r"}) == {
            "as_of": _bound("d1")
        }

    def test_falls_back_to_the_param_alias(self) -> None:
        assert declared_input_bindings(BOUND, {"AS_OF": "d1"}) == {"as_of": _bound("d1")}

    def test_binds_an_unset_optional_input_to_unset(self) -> None:
        spec = _spec(as_of=_new_run("AS_OF", required=False))
        assert declared_input_bindings(spec, {}) == {"as_of": _bound(None)}

    def test_empty_for_a_process_binding_nothing(self) -> None:
        assert declared_input_bindings(RECORD_ONLY, {"as_of": "d1"}) == {}


class TestCompare:
    def test_a_launch_at_the_recorded_values_changes_nothing(self) -> None:
        changes = compare_input_bindings({"as_of": _bound("d1")}, BOUND, {"as_of": "d1"})
        assert changes == InputBindingChanges(changed={}, released={}, adopted={})
        assert not changes.refuses
        assert not changes.rewrites

    def test_a_different_value_is_a_change(self) -> None:
        changes = compare_input_bindings({"as_of": _bound("d1")}, BOUND, {"as_of": "d2"})
        assert changes.changed == {"as_of": ("d1", "d2")}
        assert changes.refuses

    def test_an_unset_value_is_a_change(self) -> None:
        changes = compare_input_bindings({"as_of": _bound("d1")}, BOUND, {})
        assert changes.changed == {"as_of": ("d1", None)}

    def test_a_binding_the_process_no_longer_declares_is_released_at_its_value(self) -> None:
        changes = compare_input_bindings({"as_of": _bound("d1")}, RECORD_ONLY, {"as_of": "d1"})
        assert changes.released == {"as_of": _bound("d1")}
        assert changes.changed == {}
        assert changes.rewrites

    def test_a_binding_the_process_no_longer_declares_still_holds_at_another_value(self) -> None:
        changes = compare_input_bindings({"as_of": _bound("d1")}, RECORD_ONLY, {"as_of": "d2"})
        assert changes.changed == {"as_of": ("d1", "d2")}
        assert changes.released == {}

    def test_a_binding_of_an_input_the_process_removed_is_read_by_its_recorded_alias(self) -> None:
        """The record has no alias; an input the spec dropped compares by logical name only."""
        spec = _spec(code_rev=_record("CODE_REV"))
        changes = compare_input_bindings({"as_of": _bound("d1")}, spec, {"AS_OF": "d1"})
        assert changes.changed == {"as_of": ("d1", None)}
        released = compare_input_bindings({"as_of": _bound("d1")}, spec, {"as_of": "d1"})
        assert released.released == {"as_of": _bound("d1")}

    def test_a_declared_binding_the_record_lacks_is_adopted(self) -> None:
        changes = compare_input_bindings({}, BOUND, {"as_of": "d2"})
        assert changes.adopted == {"as_of": _bound("d2")}
        assert changes.rewrites

    def test_a_renamed_alias_is_no_change(self) -> None:
        renamed = _spec(as_of=_new_run("AS_OF_DATE"))
        changes = compare_input_bindings(
            {"as_of": _bound("d1")}, renamed, {"as_of": "d1", "AS_OF_DATE": "d1"}
        )
        assert changes == InputBindingChanges(changed={}, released={}, adopted={})

    def test_a_runs_dir_input_compares_across_the_filestore_mount_aliases(self) -> None:
        spec = _spec(runs_dir={"param": "RUNS_DIR", "as": "path", "on_change": "new_run"})
        changes = compare_input_bindings(
            {"runs_dir": _bound("/mnt/disks/filestore/runs")},
            spec,
            {"runs_dir": "/mnt/filestore/runs"},
        )
        assert changes.changed == {}
        other = compare_input_bindings(
            {"runs_dir": _bound("/mnt/disks/filestore/runs")},
            spec,
            {"runs_dir": "/workspace/mnt/filestore/runs"},
        )
        assert other.changed == {
            "runs_dir": ("/mnt/disks/filestore/runs", "/workspace/mnt/filestore/runs")
        }


class TestApply:
    def test_drops_released_and_adds_adopted(self) -> None:
        recorded = {"as_of": _bound("d1"), "scope": _bound("s1")}
        changes = InputBindingChanges(
            changed={}, released={"scope": _bound("s1")}, adopted={"region": _bound("eu")}
        )
        assert apply_input_binding_changes(recorded, changes) == {
            "as_of": _bound("d1"),
            "region": _bound("eu"),
        }

    def test_refusing_changes_cannot_be_applied(self) -> None:
        changes = InputBindingChanges(changed={"as_of": ("d1", "d2")}, released={}, adopted={})
        with pytest.raises(ValueError, match=r"refuse"):
            apply_input_binding_changes({"as_of": _bound("d1")}, changes)


class TestRefusal:
    def test_nothing_changed_is_no_refusal(self) -> None:
        changes = InputBindingChanges(changed={}, released={}, adopted={"as_of": _bound("d1")})
        assert (
            input_binding_refusal(changes, spec=BOUND, record_path=Path("r"), launch="Resume")
            is None
        )

    def test_names_the_launch_the_count_the_record_and_each_input(self) -> None:
        changes = InputBindingChanges(
            changed={"scope": ("s1", None), "as_of": ("d1", "d2")}, released={}, adopted={}
        )
        spec = _spec(as_of=_new_run("AS_OF"), scope=_new_run("SCOPE"))

        message = input_binding_refusal(
            changes,
            spec=spec,
            record_path=Path("/run/.state/input-bindings.yaml"),
            launch="run-step",
        )

        assert message is not None
        assert message.splitlines() == [
            (
                "run-step refused: this launch changes 2 inputs bound `on_change: new_run` in "
                "/run/.state/input-bindings.yaml:"
            ),
            "  as_of: 'd1' -> 'd2'",
            "  scope: 's1' -> <unset>",
            (
                "A `new_run` input holds one value for the life of a run: the run's task state, "
                "results, and summaries all describe the recorded value. Resume with the "
                "recorded values, or start a new RUN_ID for the new ones."
            ),
        ]

    def test_marks_an_input_the_process_no_longer_binds(self) -> None:
        changes = InputBindingChanges(changed={"as_of": ("d1", "d2")}, released={}, adopted={})

        message = input_binding_refusal(
            changes, spec=RECORD_ONLY, record_path=Path("r"), launch="Resume"
        )

        assert message is not None
        assert (
            "  as_of: 'd1' -> 'd2' (no longer declared `on_change: new_run`; a binding is "
            "released only at its recorded value)"
        ) in message
        assert message.endswith(
            "Resume with the recorded value, or start a new RUN_ID for the new one."
        )


class TestRecord:
    def test_round_trips_through_the_scope_directory(self, tmp_path: Path) -> None:
        record = InputBindingsRecord(
            run_id="mine/run-1", scope_path=["prelim", "ACME"], bindings={"as_of": _bound("d1")}
        )

        path = write_input_bindings(tmp_path, record)

        assert path == input_bindings_file(tmp_path) == tmp_path / STATE_DIR / "input-bindings.yaml"
        assert read_input_bindings(tmp_path) == record
        assert recorded_input_bindings(tmp_path) == {"as_of": _bound("d1")}

    def test_absent_means_nothing_bound(self, tmp_path: Path) -> None:
        assert read_input_bindings(tmp_path) is None
        assert recorded_input_bindings(tmp_path) == {}

    def test_a_record_without_its_envelope_is_an_error(self, tmp_path: Path) -> None:
        (tmp_path / STATE_DIR).mkdir()
        (tmp_path / STATE_DIR / "input-bindings.yaml").write_text(
            "bindings: {}\n", encoding="utf-8"
        )
        with pytest.raises(ValueError, match=r"expected an input_bindings envelope"):
            read_input_bindings(tmp_path)

    def test_the_record_and_its_bindings_forbid_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            InputBindingsRecord.model_validate({"run_id": "r", "bindings": {}, "extra": 1})
        with pytest.raises(ValidationError):
            InputBinding.model_validate({"on_change": "new_run", "value": "d1", "param": "AS_OF"})

    def test_only_new_run_binds(self) -> None:
        with pytest.raises(ValidationError):
            InputBinding.model_validate({"on_change": "record", "value": "d1"})
