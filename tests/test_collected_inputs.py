"""Collected inputs: building, recording, and comparing a consumer's fan-in documents.

A ``collect:`` consumer is reused on resume only while every document it was last
handed still describes the same outcomes. These tests cover the engine half: the
documents are built from durable per-item state, the record holds one digest per
document, and a comparison reports a change only when an item's outcome moved. A
record that is present but unreadable raises to the caller, which counts it as
changed, and the next delivery replaces it.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest
import yaml

from metaproc.engine.collected_inputs import (
    COLLECTED_INPUTS_UNREADABLE,
    changed_collected_inputs,
    collected_documents,
    read_collected_inputs,
    record_collected_inputs,
)
from metaproc.engine.fan_in import build_outcome_manifest
from metaproc.io import read_yaml_file
from metaproc.io.state_io import read_collected_inputs_at
from metaproc.models.authored import IOSpec
from metaproc.models.plan import FanOut, ResolvedStep
from metaproc.paths import COLLECTED_INPUTS_FILE, STATE_DIR, TASKS_SUBDIR


def _task(run_dir: Path, step: str, key: str, state: str, error: str = "") -> None:
    state_dir = run_dir / STATE_DIR / TASKS_SUBDIR / step / key
    state_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "run_id": "r",
        "step_id": step,
        "item": {"item": key},
        "state": state,
        "attempt": 1,
    }
    if error:
        payload["error"] = error
    (state_dir / "status.yaml").write_text(yaml.safe_dump(payload), encoding="utf-8")


def _scan(keys: tuple[str, ...] = ("a", "b")) -> ResolvedStep:
    return ResolvedStep(
        step_id="scan",
        mode="code",
        fan_out=FanOut(
            over="roster", bind="item", source="roster.md", items=[{"item": k} for k in keys]
        ),
    )


def _consumer() -> ResolvedStep:
    return ResolvedStep(
        step_id="summarize",
        mode="code",
        inputs={
            "outcomes": IOSpec(path="{{RUN_DIR}}/summary/outcomes.yaml", collect="scan"),
            "notes": IOSpec(path="{{RUN_DIR}}/notes.md"),
        },
        needs=["scan"],
    )


def _record_path(run_dir: Path, step_id: str) -> Path:
    return run_dir / STATE_DIR / TASKS_SUBDIR / step_id / COLLECTED_INPUTS_FILE


# A record without the outcome digest (an earlier shape) and one that is not YAML.
_UNREADABLE_RECORDS = pytest.mark.parametrize(
    "text",
    [
        (
            "schema: metaproc:CollectedInputs/0.1\nrun_id: r\nstep_id: summarize\n"
            "recorded_at: '2026-09-22T00:00:00'\n"
            "inputs:\n  outcomes:\n    upstream_step: scan\n    path: outcomes.yaml\n"
        ),
        "inputs: [unclosed\n",
    ],
    ids=["no-outcome-digest", "malformed-yaml"],
)


def _write_record(run_dir: Path, text: str) -> Path:
    record_path = _record_path(run_dir, "summarize")
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(text, encoding="utf-8")
    return record_path


def _variables(run_dir: Path) -> dict[str, str]:
    return {"RUN_DIR": str(run_dir)}


def _changed(run_dir: Path, *, keys: tuple[str, ...] = ("a", "b")) -> list[str]:
    documents = changed_collected_inputs(
        run_dir,
        _consumer(),
        step_map={"scan": _scan(keys)},
        chains_by_member={},
        variables=_variables(run_dir),
    )
    return [document.input_name for document in documents]


def _deliver(run_dir: Path, *, run_id: str = "r") -> None:
    """Build and record the consumer's documents, as delivery does."""
    documents = collected_documents(
        _consumer(),
        step_map={"scan": _scan()},
        chains_by_member={},
        variables=_variables(run_dir),
        run_dir=run_dir,
    )
    record_collected_inputs(run_dir, run_id=run_id, step_id="summarize", documents=documents)


class TestCollectedDocuments:
    def test_one_document_per_collect_input_against_the_collected_roster(
        self, tmp_path: Path
    ) -> None:
        _task(tmp_path, "scan", "a", "completed")
        _task(tmp_path, "scan", "b", "failed", error="boom")

        documents = collected_documents(
            _consumer(),
            step_map={"scan": _scan(("a", "b", "c"))},
            chains_by_member=None,
            variables=_variables(tmp_path),
            run_dir=tmp_path,
        )

        assert [(d.input_name, d.upstream_step) for d in documents] == [("outcomes", "scan")]
        document = documents[0]
        assert document.path == tmp_path / "summary" / "outcomes.yaml"
        block = document.manifest.payload["fan_in_outcomes"]
        assert (block["total"], block["succeeded"], block["failed"]) == (3, 1, 2)
        states = {item["key"]: item["state"] for item in block["items"]}
        assert states == {"a": "completed", "b": "failed", "c": "not_reached"}
        # Building writes nothing; delivery writes the document.
        assert not document.path.exists()

    def test_an_item_that_never_arrived_names_where_it_stopped(self, tmp_path: Path) -> None:
        _task(tmp_path, "fetch", "a", "completed")
        _task(tmp_path, "fetch", "b", "failed", error="fetch refused")
        _task(tmp_path, "scan", "a", "completed")

        documents = collected_documents(
            _consumer(),
            step_map={"scan": _scan()},
            # The chain continues past the collected step; only its upstream part counts.
            chains_by_member={"scan": ["fetch", "scan", "after"]},
            variables=_variables(tmp_path),
            run_dir=tmp_path,
        )

        items = {i["key"]: i for i in documents[0].manifest.payload["fan_in_outcomes"]["items"]}
        assert items["b"]["state"] == "not_reached"
        assert items["b"]["stopped_at"] == "fetch"
        assert items["b"]["error"] == "fetch refused"

    def test_a_step_without_collect_inputs_has_no_documents(self, tmp_path: Path) -> None:
        assert (
            collected_documents(
                _scan(),
                step_map=None,
                chains_by_member=None,
                variables=_variables(tmp_path),
                run_dir=tmp_path,
            )
            == []
        )


class TestRecordCollectedInputs:
    def test_the_record_holds_one_outcome_digest_per_document(self, tmp_path: Path) -> None:
        _task(tmp_path, "scan", "a", "completed")
        _task(tmp_path, "scan", "b", "failed", error="boom (traceback: .logs/b/att-1.log)")

        _deliver(tmp_path)

        raw = read_yaml_file(_record_path(tmp_path, "summarize"))
        assert raw["inputs"] == {
            "outcomes": {
                "upstream_step": "scan",
                "path": str(tmp_path / "summary" / "outcomes.yaml"),
                "outcomes_sha256": build_outcome_manifest(
                    tmp_path, "scan", ["a", "b"]
                ).outcomes_sha256,
            }
        }

    def test_an_unchanged_record_is_left_as_it_is(self, tmp_path: Path) -> None:
        _task(tmp_path, "scan", "a", "completed")
        _task(tmp_path, "scan", "b", "failed", error="boom (traceback: .logs/b/att-1.log)")
        _deliver(tmp_path)
        record_path = _record_path(tmp_path, "summarize")
        # Age the record so a rewrite is observable.
        os.utime(record_path, ns=(0, 0))

        # A new attempt's log path is not an outcome change.
        _task(tmp_path, "scan", "b", "failed", error="boom (traceback: .logs/b/att-2.log)")
        _deliver(tmp_path)
        assert record_path.stat().st_mtime_ns == 0

        # A different run rewrites it.
        _deliver(tmp_path, run_id="other")
        record = read_collected_inputs_at(record_path.parent)
        assert record is not None
        assert record.run_id == "other"

    @_UNREADABLE_RECORDS
    def test_an_unreadable_record_is_replaced(self, tmp_path: Path, text: str) -> None:
        _task(tmp_path, "scan", "a", "completed")
        _task(tmp_path, "scan", "b", "failed", error="boom")
        record_path = _write_record(tmp_path, text)

        _deliver(tmp_path)

        record = read_collected_inputs_at(record_path.parent)
        assert record is not None
        assert record.inputs["outcomes"].outcomes_sha256 == (
            build_outcome_manifest(tmp_path, "scan", ["a", "b"]).outcomes_sha256
        )


class TestReadCollectedInputs:
    def test_a_missing_record_reads_as_absent(self, tmp_path: Path) -> None:
        assert read_collected_inputs(tmp_path, "summarize") is None

    @_UNREADABLE_RECORDS
    def test_an_unreadable_record_raises(self, tmp_path: Path, text: str) -> None:
        """Absent and unreadable are different facts; only absent reads as None."""
        _write_record(tmp_path, text)

        with pytest.raises(COLLECTED_INPUTS_UNREADABLE):
            read_collected_inputs(tmp_path, "summarize")


class TestChangedCollectedInputs:
    def test_nothing_changed_while_every_outcome_holds(self, tmp_path: Path) -> None:
        _task(tmp_path, "scan", "a", "completed")
        _task(tmp_path, "scan", "b", "failed", error="boom (traceback: .logs/b/att-1.log)")
        _deliver(tmp_path)

        assert _changed(tmp_path) == []
        # A new message and a new log path for the same failure are not a change.
        _task(tmp_path, "scan", "b", "failed", error="bang (traceback: .logs/b/att-2.log)")
        assert _changed(tmp_path) == []

    def test_a_completed_item_is_a_change(self, tmp_path: Path) -> None:
        _task(tmp_path, "scan", "a", "completed")
        _task(tmp_path, "scan", "b", "failed", error="boom")
        _deliver(tmp_path)

        _task(tmp_path, "scan", "b", "completed")

        assert _changed(tmp_path) == ["outcomes"]

    def test_a_widened_roster_is_a_change(self, tmp_path: Path) -> None:
        _task(tmp_path, "scan", "a", "completed")
        _task(tmp_path, "scan", "b", "completed")
        _deliver(tmp_path)

        assert _changed(tmp_path, keys=("a", "b", "c")) == ["outcomes"]

    def test_no_record_is_not_compared(self, tmp_path: Path) -> None:
        _task(tmp_path, "scan", "a", "completed")
        _task(tmp_path, "scan", "b", "failed", error="boom")

        assert _changed(tmp_path) == []

    def test_a_record_without_the_outcome_digest_raises_to_the_caller(self, tmp_path: Path) -> None:
        """The comparison does not decide what an unreadable record means; the
        orchestrator counts it as changed."""
        _task(tmp_path, "scan", "a", "completed")
        _task(tmp_path, "scan", "b", "failed", error="boom")
        _deliver(tmp_path)
        record_path = _record_path(tmp_path, "summarize")
        record_path.write_text(
            "\n".join(
                line
                for line in record_path.read_text(encoding="utf-8").splitlines()
                if "outcomes_sha256" not in line
            )
            + "\n",
            encoding="utf-8",
        )

        with pytest.raises(COLLECTED_INPUTS_UNREADABLE, match="outcomes_sha256"):
            _changed(tmp_path)

    @_UNREADABLE_RECORDS
    def test_an_unreadable_record_raises_to_the_caller(self, tmp_path: Path, text: str) -> None:
        _task(tmp_path, "scan", "a", "completed")
        _write_record(tmp_path, text)

        with pytest.raises(COLLECTED_INPUTS_UNREADABLE):
            _changed(tmp_path)

    def test_a_supplied_record_is_compared_without_reading_the_file(self, tmp_path: Path) -> None:
        """A caller that has read the record passes it, and the file is not read again."""
        _task(tmp_path, "scan", "a", "completed")
        _task(tmp_path, "scan", "b", "failed", error="boom")
        _deliver(tmp_path)
        recorded = read_collected_inputs(tmp_path, "summarize")
        assert recorded is not None
        # The file no longer reads, so only the supplied record can be compared.
        _write_record(tmp_path, "inputs: [unclosed\n")
        _task(tmp_path, "scan", "b", "completed")

        changed = changed_collected_inputs(
            tmp_path,
            _consumer(),
            step_map={"scan": _scan()},
            chains_by_member={},
            variables=_variables(tmp_path),
            recorded=recorded,
        )

        assert [document.input_name for document in changed] == ["outcomes"]

    def test_an_input_the_record_does_not_name_is_not_compared(self, tmp_path: Path) -> None:
        _task(tmp_path, "scan", "a", "completed")
        _task(tmp_path, "scan", "b", "failed", error="boom")
        # The record names only an input the consumer no longer declares.
        [document] = collected_documents(
            _consumer(),
            step_map={"scan": _scan()},
            chains_by_member={},
            variables=_variables(tmp_path),
            run_dir=tmp_path,
        )
        record_collected_inputs(
            tmp_path,
            run_id="r",
            step_id="summarize",
            documents=[dataclasses.replace(document, input_name="earlier")],
        )
        _task(tmp_path, "scan", "b", "completed")

        assert _changed(tmp_path) == []

    def test_a_step_without_collect_inputs_is_never_changed(self, tmp_path: Path) -> None:
        assert (
            changed_collected_inputs(
                tmp_path,
                _scan(),
                step_map={},
                chains_by_member={},
                variables=_variables(tmp_path),
            )
            == []
        )
