"""Runtime step resource sampling tests."""

from __future__ import annotations

from pathlib import Path

from metaproc.engine.resource_sampling import sample_step_resources
from metaproc.logutil.resource_events import read_events
from metaproc.models.resources import SampleEvent
from metaproc.paths import LOGS_DIR, RESOURCE_EVENTS_FILE, run_config_file


def test_composite_step_samples_land_in_root_ledger(tmp_path: Path) -> None:
    root_run_dir = tmp_path / "run"
    config_path = run_config_file(root_run_dir)
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{}")
    child_run_dir = root_run_dir / "outer" / "inner"
    child_run_dir.mkdir(parents=True)

    with sample_step_resources(
        run_dir=child_run_dir,
        run_id="process/run-1/outer/inner",
        step_node_id="analyze",
    ):
        pass

    event_path = root_run_dir / LOGS_DIR / RESOURCE_EVENTS_FILE
    samples = [event for event in read_events(event_path) if isinstance(event, SampleEvent)]
    assert samples
    assert {event.hierarchy.run_id for event in samples} == {"process/run-1"}
    assert {event.hierarchy.step_node_id for event in samples} == {"outer::inner::analyze"}
    assert not (child_run_dir / LOGS_DIR / RESOURCE_EVENTS_FILE).exists()
