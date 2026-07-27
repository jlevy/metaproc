"""Phase 2 /api/resources payload contracts (spec §8.4, the original design)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from metabrowser import server as proc_browser

from metaproc.metabrowser_plugin import sidekick


class _FakeRequest:
    def __init__(self, **params: str) -> None:
        self.query_params = _Params(params)


class _Params:
    def __init__(self, data: dict[str, str]) -> None:
        self._data = data

    def get(self, key: str, default: str = "") -> str:
        return self._data.get(key, default)


def _call(**params: str) -> tuple[int, dict[str, Any]]:
    response = sidekick.resources_handler(cast(Any, _FakeRequest(**params)))
    body = json.loads(bytes(response.body).decode())
    return response.status_code, body


_PARENT_PROCESS = """\
---
process:
  name: parent
  steps:
    - id: predict
      mode: code
      handler: metaproc.code_steps.scaffold
---

Parent body.
"""


def _make_pi_log_with_tool_call(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(
            {"type": "agent_start", "model": "claude-opus-4-7", "timestamp": "2026-04-24T12:00:00Z"}
        ),
        json.dumps(
            {
                "type": "tool_execution_start",
                "toolName": "Bash",
                "executionId": "ex-1",
                "args": {"command": "ls"},
                "timestamp": "2026-04-24T12:00:01Z",
            }
        ),
        json.dumps(
            {
                "type": "tool_execution_end",
                "toolName": "Bash",
                "executionId": "ex-1",
                "isError": False,
                "result": {"content": "file.txt"},
                "timestamp": "2026-04-24T12:00:03Z",
            }
        ),
        json.dumps(
            {
                "type": "agent_end",
                "messages": [
                    {
                        "role": "assistant",
                        "model": "claude-opus-4-7",
                        "provider": "anthropic",
                        "usage": {
                            "input": 100,
                            "output": 50,
                            "cacheRead": 0,
                            "cacheWrite": 0,
                            "cost": {"total": 0.42},
                        },
                    }
                ],
                "timestamp": "2026-04-24T12:00:05Z",
            }
        ),
    ]
    path.write_text("\n".join(lines) + "\n")


def _write(tmp_path: Path, rel: str, content: str) -> Path:
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


def _setup(tmp_path: Path) -> tuple[str, str]:
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    run_dir = tmp_path / "runs" / "2026-04-21"
    run_dir.mkdir(parents=True)
    _make_pi_log_with_tool_call(run_dir / "predict" / "AAPL" / ".logs" / "session.jsonl")
    proc_browser._set_root_dir(tmp_path)
    return (
        str(parent.relative_to(tmp_path)),
        str(run_dir.relative_to(tmp_path)),
    )


def test_api_resources_payload_carries_taxonomy_rollups_dict(tmp_path: Path) -> None:
    """The consumer view reads taxonomy rollups as path, canonical, and metrics records."""
    process_rel, run_dir_rel = _setup(tmp_path)
    status, payload = _call(run_dir=run_dir_rel, process=process_rel)
    assert status == 200
    rollups = payload["taxonomy_rollups"]
    assert isinstance(rollups, dict)
    # Every persisted family is a list of entries, not a dict.
    for family, entries in rollups.items():
        assert isinstance(entries, list), f"{family} must be a list"
        for entry in entries:
            assert set(entry.keys()) >= {"path", "canonical", "metrics"}
            assert isinstance(entry["path"], list)
            assert isinstance(entry["canonical"], str)


def test_api_resources_payload_carries_hierarchy_root_with_children(
    tmp_path: Path,
) -> None:
    """Treemap needs a recursive children array with total_metrics on each node."""
    process_rel, run_dir_rel = _setup(tmp_path)
    status, payload = _call(run_dir=run_dir_rel, process=process_rel)
    assert status == 200
    root = payload["hierarchy_root"]
    assert root["node_type"] == "run"
    assert isinstance(root["children"], list)
    assert isinstance(root["total_metrics"], dict)


def test_api_resources_source_logs_include_owner_and_summary(tmp_path: Path) -> None:
    """Grouped log evidence panel (P2-4) needs owner_node_id + summary fields."""
    process_rel, run_dir_rel = _setup(tmp_path)
    status, payload = _call(run_dir=run_dir_rel, process=process_rel)
    assert status == 200
    logs = payload["source_logs"]
    assert logs, "expected at least one source log to be inventoried"
    for log in logs:
        assert "owner_node_id" in log
        assert "kind" in log
        assert "adapter" in log
        assert "summary" in log
        assert {"event_count", "error_count", "tool_call_count"} <= set(log["summary"])
