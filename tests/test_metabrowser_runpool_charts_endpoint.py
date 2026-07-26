"""Runpool chart extraction must dispatch on the adapter, not the metabrowser kind.

Regression for the internal review: after Phase 2 moved runpool/process
kinds out of metabrowser core into the metaproc plugin's manifest,
``classify_by_ext('.jsonl', 'runpool')`` correctly returns
``unknown-jsonl`` (the manifest classifier owns runpool now). The
sidekick's old code branched on the *kind* and silently returned
empty charts for runpool logs.

Fix: dispatch on the adapter directly. This test mocks the underlying
extractor and asserts the right one was called for each adapter — the
real extractors have their own metaproc-domain test coverage.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import Mock, patch

from metabrowser import server

from metaproc.metabrowser_plugin import sidekick

_RUNPOOL_HEAD = '{"event": "pool_start", "pool_id": "p1", "backend": "process"}\n'
_AGENT_HEAD = '{"type": "system", "subtype": "init", "session_id": "s1"}\n'


class _FakeQuery:
    def __init__(self, params: dict[str, str]) -> None:
        self._params = params

    def get(self, key: str, default: str = "") -> str:
        return self._params.get(key, default)


def _make_request(path: str) -> Mock:
    request = Mock(spec=["query_params", "headers"])
    request.query_params = _FakeQuery({"path": path})
    request.headers = {}
    return request


def test_charts_handler_dispatches_runpool_adapter_to_runpool_extractor(
    tmp_path: Path,
) -> None:
    """Runpool JSONL must call the metaproc extractor, not agent."""

    server._set_root_dir(tmp_path)  # noqa: SLF001
    log = tmp_path / "events.jsonl"
    log.write_text(_RUNPOOL_HEAD)

    sentinel = {"summary": {"runpool": "ok"}, "charts": [{"id": "rp"}]}
    with (
        patch(
            "metaproc.metabrowser_plugin.charts.extract_runpool_charts", return_value=sentinel
        ) as rp_mock,
        patch("metaproc.metabrowser_plugin.sidekick.extract_agent_charts_cached") as ag_mock,
    ):
        response = asyncio.run(sidekick.charts_handler(_make_request("events.jsonl")))

    payload = json.loads(bytes(response.body))
    assert payload == sentinel
    rp_mock.assert_called_once()
    ag_mock.assert_not_called()


def test_charts_handler_dispatches_agent_adapter_to_agent_extractor(tmp_path: Path) -> None:
    """Claude/Gemini/Pi JSONL must call extract_agent_charts_cached."""

    server._set_root_dir(tmp_path)  # noqa: SLF001
    log = tmp_path / "session.jsonl"
    log.write_text(_AGENT_HEAD)

    sentinel = {"summary": {"agent": "ok"}, "charts": [{"id": "ag"}]}
    with (
        patch("metaproc.metabrowser_plugin.charts.extract_runpool_charts") as rp_mock,
        patch(
            "metaproc.metabrowser_plugin.sidekick.extract_agent_charts_cached",
            return_value=sentinel,
        ) as ag_mock,
    ):
        response = asyncio.run(sidekick.charts_handler(_make_request("session.jsonl")))

    payload = json.loads(bytes(response.body))
    assert payload == sentinel
    ag_mock.assert_called_once()
    rp_mock.assert_not_called()


def test_charts_handler_returns_empty_for_unknown_adapter(tmp_path: Path) -> None:
    """Adapters that aren't runpool/claude/gemini/pi get an empty payload."""

    server._set_root_dir(tmp_path)  # noqa: SLF001
    log = tmp_path / "weird.jsonl"
    log.write_text('{"some": "shape we do not recognise"}\n')

    with (
        patch("metaproc.metabrowser_plugin.charts.extract_runpool_charts") as rp_mock,
        patch("metaproc.metabrowser_plugin.sidekick.extract_agent_charts_cached") as ag_mock,
    ):
        response = asyncio.run(sidekick.charts_handler(_make_request("weird.jsonl")))

    payload = json.loads(bytes(response.body))
    assert payload == {"summary": None, "charts": []}
    rp_mock.assert_not_called()
    ag_mock.assert_not_called()
