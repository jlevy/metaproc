"""Tests for /api/resources (B13, spec §8.6).

Tests call the handler coroutine directly so we don't pull httpx as a
dependency just for one endpoint — matches the broader pattern in this
test suite (no other browser test spins up TestClient).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from metabrowser import server as proc_browser

from metaproc.metabrowser_plugin import sidekick

_PARENT_PROCESS = """\
---
process:
  name: parent
  deps:
    child_proc:
      path: child/test.process.md
      role: process
      as: path
  steps:
    - id: run_child
      mode: composite
      uses: deps.child_proc
      with:
        CUTOFF_DATE: "{{CUTOFF_DATE}}"
---

Parent body.
"""

_CHILD_PROCESS = """\
---
process:
  name: child
  steps:
    - id: leaf
      mode: code
      handler: metaproc.code_steps.scaffold
---

Child body.
"""


def _write(tmp_path: Path, rel: str, content: str) -> Path:
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


def _make_claude_log(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "model": "claude-opus-4-7",
                "session_id": "abc",
                "timestamp": "2026-04-24T12:00:00Z",
            }
        ),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "total_cost_usd": 0.42,
                "duration_ms": 1500,
                "usage": {"input_tokens": 100, "output_tokens": 50},
                "timestamp": "2026-04-24T12:00:01.5Z",
            }
        ),
    ]
    path.write_text("\n".join(lines) + "\n")


class _FakeRequest:
    """Minimal Request stand-in: only ``query_params`` is touched by api_resources."""

    def __init__(self, **params: str) -> None:
        self.query_params = _Params(params)


class _Params:
    def __init__(self, data: dict[str, str]) -> None:
        self._data = data

    def get(self, key: str, default: str = "") -> str:
        return self._data.get(key, default)


def _setup(tmp_path: Path) -> tuple[str, str]:
    """Build a fixture; return (process_rel, run_dir_rel)."""
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    _write(tmp_path, "parent/child/test.process.md", _CHILD_PROCESS)
    run_dir = tmp_path / "runs" / "2026-04-21"
    run_dir.mkdir(parents=True)
    proc_browser._set_root_dir(tmp_path)
    return (
        str(parent.relative_to(tmp_path)),
        str(run_dir.relative_to(tmp_path)),
    )


def _call(**params: str) -> tuple[int, dict[str, Any]]:
    """Run api_resources to completion and return (status, parsed body)."""
    fake = _FakeRequest(**params)
    # api_resources only reads `request.query_params.get(...)`, so a duck-typed
    # stand-in is sufficient. Cast through Any to keep basedpyright happy without
    # importing starlette internals into the test fixtures.
    response = sidekick.resources_handler(fake)  # pyright: ignore[reportArgumentType]
    body = json.loads(bytes(response.body).decode())
    return response.status_code, body


def test_first_call_builds_when_no_cache(tmp_path: Path) -> None:
    process_rel, run_dir_rel = _setup(tmp_path)
    status, payload = _call(run_dir=run_dir_rel, process=process_rel)
    assert status == 200, payload
    assert payload["schema"] == "metaproc.resources/v2"
    assert payload["hierarchy_root"]["node_type"] == "run"
    assert (tmp_path / run_dir_rel / "resources.json").exists()


def test_cached_call_does_not_require_process_param(tmp_path: Path) -> None:
    process_rel, run_dir_rel = _setup(tmp_path)
    _call(run_dir=run_dir_rel, process=process_rel)  # prime cache

    status, payload = _call(run_dir=run_dir_rel)
    assert status == 200
    assert payload["schema"] == "metaproc.resources/v2"


def test_cached_v1_document_remains_available_through_api(tmp_path: Path) -> None:
    _process_rel, run_dir_rel = _setup(tmp_path)
    cache = tmp_path / run_dir_rel / "resources.json"
    cache.write_text(
        json.dumps(
            {
                "schema": "metaproc.resources/v1",
                "run_id": "run-legacy",
                "generated_at": "2026-04-24T12:00:00Z",
                "source_events_path": ".logs/resource-events.jsonl",
                "hierarchy_root": {
                    "node_type": "run",
                    "node_id": "run-legacy",
                    "label": "legacy",
                },
            }
        )
    )

    status, payload = _call(run_dir=run_dir_rel)

    assert status == 200
    assert payload["schema"] == "metaproc.resources/v1"


def test_missing_process_when_no_cache_returns_400(tmp_path: Path) -> None:
    _process_rel, run_dir_rel = _setup(tmp_path)
    status, payload = _call(run_dir=run_dir_rel)
    assert status == 400
    assert "process" in payload["error"].lower()


def test_run_dir_outside_root_rejected(tmp_path: Path) -> None:
    _setup(tmp_path)
    status, _payload = _call(run_dir="../../../etc")
    assert status == 400


def test_refresh_param_overwrites_existing_cache(tmp_path: Path) -> None:
    process_rel, run_dir_rel = _setup(tmp_path)
    cache = tmp_path / run_dir_rel / "resources.json"
    cache.write_text("{}")  # garbage

    status, _ = _call(run_dir=run_dir_rel, process=process_rel, refresh="1")
    assert status == 200
    assert json.loads(cache.read_text())["schema"] == "metaproc.resources/v2"


def test_cached_call_rejects_cache_symlink_outside_served_root(tmp_path: Path) -> None:
    _process_rel, run_dir_rel = _setup(tmp_path / "root")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "resources.json"
    secret.write_text('{"schema": "outside-secret"}')
    cache = tmp_path / "root" / run_dir_rel / "resources.json"
    cache.symlink_to(secret)

    status, payload = _call(run_dir=run_dir_rel)

    assert status == 400
    assert "outside" in payload["error"]


def test_refresh_rejects_cache_symlink_outside_served_root(tmp_path: Path) -> None:
    process_rel, run_dir_rel = _setup(tmp_path / "root")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "resources.json"
    secret.write_text("do not overwrite")
    cache = tmp_path / "root" / run_dir_rel / "resources.json"
    cache.symlink_to(secret)

    status, payload = _call(run_dir=run_dir_rel, process=process_rel, refresh="1")

    assert status == 400
    assert "outside" in payload["error"]
    assert secret.read_text() == "do not overwrite"


def test_refresh_rejects_events_directory_symlink_outside_served_root(tmp_path: Path) -> None:
    process_rel, run_dir_rel = _setup(tmp_path / "root")
    outside_logs = tmp_path / "outside-logs"
    outside_logs.mkdir()
    run_dir = tmp_path / "root" / run_dir_rel
    (run_dir / ".logs").symlink_to(outside_logs, target_is_directory=True)

    status, payload = _call(run_dir=run_dir_rel, process=process_rel, refresh="1")

    assert status == 400
    assert "outside" in payload["error"]
    assert not (outside_logs / "resource-events.jsonl").exists()


def test_stale_flag_set_when_events_log_newer_than_cache(tmp_path: Path) -> None:
    process_rel, run_dir_rel = _setup(tmp_path)
    _call(run_dir=run_dir_rel, process=process_rel)

    events_path = tmp_path / run_dir_rel / ".logs" / "resource-events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text("{}\n")
    cache = tmp_path / run_dir_rel / "resources.json"
    cache_mtime = cache.stat().st_mtime
    os.utime(events_path, (cache_mtime + 10, cache_mtime + 10))

    status, payload = _call(run_dir=run_dir_rel)
    assert status == 200
    assert payload["stale"] is True
    assert payload["schema"] == "metaproc.resources/v2"


def test_stale_flag_false_when_events_log_older_than_cache(tmp_path: Path) -> None:
    """After F3 the build always emits both artifacts; cache-newer-than-events ⇒ not stale."""
    process_rel, run_dir_rel = _setup(tmp_path)
    _call(run_dir=run_dir_rel, process=process_rel)

    events_path = tmp_path / run_dir_rel / ".logs" / "resource-events.jsonl"
    cache = tmp_path / run_dir_rel / "resources.json"
    if events_path.exists():
        # The build wrote both atomically; bump cache mtime forward to
        # confirm the stale check returns False when events is older.
        cache_mtime = cache.stat().st_mtime
        os.utime(cache, (cache_mtime + 10, cache_mtime + 10))

    status, payload = _call(run_dir=run_dir_rel)
    assert status == 200
    assert payload["stale"] is False


def test_two_refreshes_do_not_ingest_generated_events_log(tmp_path: Path) -> None:
    process_rel, run_dir_rel = _setup(tmp_path)
    run_dir = tmp_path / run_dir_rel
    _make_claude_log(run_dir / "run_child" / "leaf" / "AAPL" / ".logs" / "session.jsonl")

    first_status, first_payload = _call(run_dir=run_dir_rel, process=process_rel, refresh="1")
    assert first_status == 200
    assert first_payload["stale"] is False

    second_status, second_payload = _call(run_dir=run_dir_rel, process=process_rel, refresh="1")
    assert second_status == 200
    assert second_payload["stale"] is False
    source_paths = {log["path"] for log in second_payload["source_logs"]}
    assert "run_child/leaf/AAPL/.logs/session.jsonl" in source_paths
    assert ".logs/resource-events.jsonl" not in source_paths
