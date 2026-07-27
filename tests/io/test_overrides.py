"""Tests for metaproc.io.overrides — run-scoped operator escape hatch I/O."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from metaproc.io import read_yaml_file
from metaproc.io.overrides import (
    OverrideEntry,
    OverridesDocument,
    clear_override,
    is_step_overridden,
    is_step_satisfied_universally,
    overrides_for_step,
    read_overrides,
    upsert_override,
)
from metaproc.paths import OVERRIDES_FILE, STATE_DIR


def _run_dir(tmp_path: Path) -> Path:
    """Create a fake run dir with a plausible name."""
    d = tmp_path / "2026-04-24-test-run"
    d.mkdir()
    return d


class TestReadOverrides:
    def test_returns_none_when_absent(self, tmp_path: Path) -> None:
        run_dir = _run_dir(tmp_path)
        assert read_overrides(run_dir) is None


class TestUpsertOverride:
    def test_creates_valid_document(self, tmp_path: Path) -> None:
        run_dir = _run_dir(tmp_path)
        upsert_override(
            run_dir,
            step="predict-ticker",
            by="levy",
            note="14/17 usable",
        )
        doc = read_overrides(run_dir)
        assert doc is not None
        assert doc.schema_ == "metaproc:OverridesDocument/0.1"
        assert doc.run_id == "2026-04-24-test-run"
        assert len(doc.entries) == 1

        entry = doc.entries[0]
        assert entry.step == "predict-ticker"
        assert entry.action == "satisfied"
        assert entry.by == "levy"
        assert entry.note == "14/17 usable"
        assert entry.item is None
        assert entry.for_downstream is None

        # Verify raw YAML shape
        raw = read_yaml_file(run_dir / STATE_DIR / OVERRIDES_FILE)
        assert raw["schema"] == "metaproc:OverridesDocument/0.1"

    def test_idempotent_same_tuple_updates_at_and_note(self, tmp_path: Path) -> None:
        run_dir = _run_dir(tmp_path)
        upsert_override(
            run_dir,
            step="predict-ticker",
            by="levy",
            note="first note",
        )
        doc1 = read_overrides(run_dir)
        assert doc1 is not None
        first_at = doc1.entries[0].at

        # Small delay so `at` differs
        time.sleep(0.01)

        upsert_override(
            run_dir,
            step="predict-ticker",
            by="levy",
            note="updated note",
        )
        doc2 = read_overrides(run_dir)
        assert doc2 is not None
        assert len(doc2.entries) == 1
        assert doc2.entries[0].note == "updated note"
        assert doc2.entries[0].at != first_at

    def test_different_for_downstream_creates_separate_entries(self, tmp_path: Path) -> None:
        run_dir = _run_dir(tmp_path)
        upsert_override(
            run_dir,
            step="predict-ticker",
            for_downstream=["summarize"],
            by="levy",
            note="for summarize only",
        )
        upsert_override(
            run_dir,
            step="predict-ticker",
            for_downstream=["qa-check"],
            by="levy",
            note="for qa-check only",
        )
        doc = read_overrides(run_dir)
        assert doc is not None
        assert len(doc.entries) == 2

    def test_step_wide_and_scoped_coexist(self, tmp_path: Path) -> None:
        run_dir = _run_dir(tmp_path)
        upsert_override(run_dir, step="a", by="levy", note="all")
        upsert_override(run_dir, step="a", for_downstream=["b"], by="levy", note="just b")
        doc = read_overrides(run_dir)
        assert doc is not None
        assert len(doc.entries) == 2


class TestClearOverride:
    def test_removes_matching_entry(self, tmp_path: Path) -> None:
        run_dir = _run_dir(tmp_path)
        upsert_override(run_dir, step="a", by="levy", note="note")
        assert clear_override(run_dir, step="a") is True
        doc = read_overrides(run_dir)
        assert doc is not None
        assert len(doc.entries) == 0

    def test_returns_false_when_no_match(self, tmp_path: Path) -> None:
        run_dir = _run_dir(tmp_path)
        upsert_override(run_dir, step="a", by="levy", note="note")
        assert clear_override(run_dir, step="nonexistent") is False

    def test_returns_false_when_no_file(self, tmp_path: Path) -> None:
        run_dir = _run_dir(tmp_path)
        assert clear_override(run_dir, step="a") is False


class TestIsStepOverridden:
    def _make_doc(self, entries: list[dict[str, Any]]) -> OverridesDocument:

        return OverridesDocument(
            run_id="test",
            entries=[OverrideEntry(**e) for e in entries],
        )

    def test_global_override_satisfies_any_downstream(self) -> None:
        doc = self._make_doc(
            [
                {"step": "a", "action": "satisfied", "by": "levy", "at": "t0"},
            ]
        )
        assert is_step_overridden(doc, "a", "b") is True
        assert is_step_overridden(doc, "a", "c") is True

    def test_scoped_override_satisfies_listed_downstream(self) -> None:
        doc = self._make_doc(
            [
                {
                    "step": "a",
                    "action": "satisfied",
                    "for_downstream": ["b"],
                    "by": "levy",
                    "at": "t0",
                },
            ]
        )
        assert is_step_overridden(doc, "a", "b") is True
        assert is_step_overridden(doc, "a", "c") is False

    def test_no_entries_returns_false(self) -> None:
        doc = self._make_doc([])
        assert is_step_overridden(doc, "a", "b") is False


class TestIsStepSatisfiedUniversally:
    """the fix — universal-satisfied check used by run-process to skip
    overridden steps on whole-DAG resume (without --from).
    """

    def _make_doc(self, entries: list[dict[str, Any]]) -> OverridesDocument:

        return OverridesDocument(
            run_id="test",
            entries=[OverrideEntry(**e) for e in entries],
        )

    def test_universal_satisfied_returns_true(self) -> None:
        doc = self._make_doc(
            [
                {"step": "mine-adhoc", "action": "satisfied", "by": "levy", "at": "t0"},
            ]
        )
        assert is_step_satisfied_universally(doc, "mine-adhoc") is True

    def test_scoped_override_does_not_satisfy_universally(self) -> None:
        """An override that targets specific downstreams should NOT count as
        universally-satisfied (the operator scoped it deliberately)."""
        doc = self._make_doc(
            [
                {
                    "step": "mine-adhoc",
                    "action": "satisfied",
                    "for_downstream": ["postprocess-adhoc-kb"],
                    "by": "levy",
                    "at": "t0",
                },
            ]
        )
        assert is_step_satisfied_universally(doc, "mine-adhoc") is False

    def test_other_action_does_not_satisfy(self) -> None:
        """Only ``satisfied`` action counts."""
        doc = self._make_doc(
            [
                {"step": "mine-adhoc", "action": "skipped", "by": "levy", "at": "t0"},
            ]
        )
        assert is_step_satisfied_universally(doc, "mine-adhoc") is False

    def test_other_step_does_not_satisfy(self) -> None:
        doc = self._make_doc(
            [
                {"step": "predict-ticker", "action": "satisfied", "by": "levy", "at": "t0"},
            ]
        )
        assert is_step_satisfied_universally(doc, "mine-adhoc") is False

    def test_no_entries_returns_false(self) -> None:
        doc = self._make_doc([])
        assert is_step_satisfied_universally(doc, "mine-adhoc") is False


class TestOverridesForStep:
    def test_filters_by_step(self) -> None:

        doc = OverridesDocument(
            run_id="test",
            entries=[
                OverrideEntry(step="a", action="satisfied", by="levy", at="t0"),
                OverrideEntry(step="b", action="satisfied", by="levy", at="t0"),
                OverrideEntry(step="a", action="satisfied", by="levy", at="t1", note="second"),
            ],
        )
        result = overrides_for_step(doc, "a")
        assert len(result) == 2
        assert all(e.step == "a" for e in result)
