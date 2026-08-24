"""Runtime step resource sampling tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Self, cast
from unittest.mock import MagicMock

import psutil
import pytest

from metaproc.engine import resource_sampling as resource_sampling_module
from metaproc.engine.resource_sampling import sample_step_resources
from metaproc.logutil.resource_events import read_events
from metaproc.models.resources import SampleEvent
from metaproc.paths import LOGS_DIR, RESOURCE_EVENTS_FILE, run_config_file


class _FakeProcess:
    def __init__(
        self,
        pid: int,
        rss: int,
        children: list[_FakeProcess] | None = None,
    ) -> None:
        self.pid = pid
        self.rss = rss
        self.direct_children = children or []

    def children(self, *, recursive: bool = False) -> list[_FakeProcess]:
        if not recursive:
            return list(self.direct_children)
        descendants: list[_FakeProcess] = []
        pending = list(self.direct_children)
        while pending:
            child = pending.pop(0)
            descendants.append(child)
            pending.extend(child.direct_children)
        return descendants

    def cpu_percent(self, interval: float | None = None) -> float:
        del interval
        return 0.0

    def memory_info(self) -> SimpleNamespace:
        return SimpleNamespace(rss=self.rss)


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


def test_in_process_sampling_excludes_preexisting_child_subtrees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sibling_grandchild = _FakeProcess(pid=3, rss=2_000)
    sibling = _FakeProcess(pid=2, rss=1_000, children=[sibling_grandchild])
    root = _FakeProcess(pid=1, rss=100, children=[sibling])
    monkeypatch.setattr(psutil, "Process", lambda pid=None: root)

    with sample_step_resources(
        run_dir=tmp_path / "run",
        run_id="process/run-1",
        step_node_id="handler",
    ) as sampler:
        sibling.direct_children.append(_FakeProcess(pid=6, rss=4_000))
        handler_grandchild = _FakeProcess(pid=5, rss=30)
        root.direct_children.append(_FakeProcess(pid=4, rss=20, children=[handler_grandchild]))

    assert [rss for _ts, _cpu, rss in sampler.stats.samples] == [100, 150]


def test_sampling_continues_when_event_log_cannot_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingLogger:
        def __init__(self, path: Path) -> None:
            del path

        def __enter__(self) -> Self:
            raise PermissionError("telemetry path is read-only")

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(resource_sampling_module, "ResourceEventLogger", _FailingLogger)

    body_executed = False
    with sample_step_resources(
        run_dir=tmp_path / "run",
        run_id="process/run-1",
        step_node_id="handler",
    ) as sampler:
        body_executed = True

    assert body_executed
    assert sampler.stats.sample_count >= 1


def test_windows_cleanup_error_does_not_replace_command_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = MagicMock(pid=1234)
    process.poll.return_value = None
    process.wait.side_effect = subprocess.TimeoutExpired("command", 1)
    process.kill.side_effect = PermissionError("access denied")
    monkeypatch.setattr(sys, "platform", "win32")

    resource_sampling_module._terminate_process_tree(
        cast("subprocess.Popen[str]", process),
    )

    process.kill.assert_called_once_with()
