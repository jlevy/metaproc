"""Fan-in collections: what a consumer of a fan-out is told.

The property under test is that a consumer can distinguish three things — an item that
succeeded, one that failed, and one that never arrived — because collapsing the third
into absence is what makes a dropped item read as full coverage.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from metaproc.engine.fan_in import collect_item_outcomes, write_outcome_manifest


def _task(run_dir: Path, step: str, key: str, state: str, error: str = "") -> None:
    d = run_dir / ".state" / "tasks" / step / key
    d.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "run_id": "r",
        "step_id": step,
        "item": {"step": step},
        "state": state,
        "attempt": 1,
    }
    if error:
        payload["error"] = error
    (d / "status.yaml").write_text(yaml.safe_dump(payload))


class TestCollectItemOutcomes:
    def test_succeeded_and_failed_items_are_distinguished(self, tmp_path: Path) -> None:
        _task(tmp_path, "s", "A", "completed")
        _task(tmp_path, "s", "B", "failed", error="boom")
        outcomes = {o["key"]: o for o in collect_item_outcomes(tmp_path, "s")}
        assert outcomes["A"]["succeeded"] is True
        assert outcomes["B"]["succeeded"] is False
        assert outcomes["B"]["error"] == "boom"

    def test_an_item_that_never_arrived_is_reported_not_absent(self, tmp_path: Path) -> None:
        """The load-bearing case: without it, a dropped item reads as full coverage."""
        _task(tmp_path, "s", "A", "completed")
        outcomes = collect_item_outcomes(tmp_path, "s", expected_keys=["A", "B"])
        by_key = {o["key"]: o for o in outcomes}
        assert set(by_key) == {"A", "B"}
        assert by_key["B"]["state"] == "not_reached"
        assert by_key["B"]["succeeded"] is False

    def test_without_expected_keys_only_what_arrived_is_seen(self, tmp_path: Path) -> None:
        """Documents the weaker reading, which is why the roster is threaded through."""
        _task(tmp_path, "s", "A", "completed")
        assert [o["key"] for o in collect_item_outcomes(tmp_path, "s")] == ["A"]

    def test_a_cached_item_counts_as_succeeded(self, tmp_path: Path) -> None:
        _task(tmp_path, "s", "A", "cached")
        assert collect_item_outcomes(tmp_path, "s")[0]["succeeded"] is True

    def test_outcomes_are_sorted_so_the_manifest_is_diffable(self, tmp_path: Path) -> None:
        for key in ("C", "A", "B"):
            _task(tmp_path, "s", key, "completed")
        assert [o["key"] for o in collect_item_outcomes(tmp_path, "s")] == ["A", "B", "C"]

    def test_an_absent_step_collects_to_nothing(self, tmp_path: Path) -> None:
        assert collect_item_outcomes(tmp_path, "never-ran") == []

    def test_an_unreadable_status_does_not_destroy_the_collection(self, tmp_path: Path) -> None:
        """One malformed record must not cost the consumer every other item's outcome."""
        _task(tmp_path, "s", "A", "completed")
        bad = tmp_path / ".state" / "tasks" / "s" / "B"
        bad.mkdir(parents=True, exist_ok=True)
        (bad / "status.yaml").write_text("run_id: r\nstep_id: s\n")
        by_key = {o["key"]: o for o in collect_item_outcomes(tmp_path, "s")}
        assert by_key["A"]["succeeded"] is True
        assert by_key["B"]["succeeded"] is False


class TestWriteOutcomeManifest:
    def test_manifest_counts_against_the_expected_roster(self, tmp_path: Path) -> None:
        _task(tmp_path, "s", "A", "completed")
        _task(tmp_path, "s", "B", "failed")
        dest = tmp_path / "out" / "outcomes.yaml"
        payload = write_outcome_manifest(tmp_path, "s", dest, expected_keys=["A", "B", "C"])
        block = payload["fan_in_outcomes"]
        assert (block["total"], block["succeeded"], block["failed"]) == (3, 1, 2)
        assert dest.is_file()
        written = yaml.safe_load(dest.read_text())["fan_in_outcomes"]
        assert written["upstream_step"] == "s"
        assert [i["key"] for i in written["items"]] == ["A", "B", "C"]
