"""api/file emits plugin-supplied views in the response.

Locks in the integration between the manifest-driven classifier
(_classify_with_plugins) and the view-list merger (_views_for_kind):

* Plugin [[kind]] rules win over the legacy detector chain when their
  priority is higher.
* Plugin [[view]] entries merge into the response alongside built-in
  VIEW_REGISTRY entries.
* Same-id plugin views override the built-in entry.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

from metabrowser import server


class _FakeQuery:
    def __init__(self, params: dict[str, str]) -> None:
        self._params = params

    def get(self, key: str, default: str = "") -> str:
        return self._params.get(key, default)


def _api_file(path: str) -> dict[str, Any]:
    request = Mock(spec=["query_params", "headers"])
    request.query_params = _FakeQuery({"path": path})
    request.headers = {}
    response = asyncio.run(server.api_file(request))
    return json.loads(response.body)


def test_metaproc_plugin_claims_process_spec_kind(tmp_path: Path) -> None:
    """A .md file with `process:` frontmatter resolves to 'process-spec' via
    the metaproc plugin's manifest classifier (priority 100), not the legacy
    chain (priority 0).
    """
    server._set_root_dir(tmp_path)  # noqa: SLF001
    md = tmp_path / "p.md"
    md.write_text("---\nprocess:\n  name: p\n---\n\nbody\n")
    result = _api_file("p.md")
    assert result["kind"] == "process-spec"
    # The legacy VIEW_REGISTRY[process-spec] views still come through —
    # the metaproc plugin doesn't redeclare them, so the built-in wins.
    view_ids = [v["id"] for v in result["views"]]
    assert "visual" in view_ids
    assert "rendered" in view_ids
    # And the metaproc plugin contributes its own "Steps" view (the
    # end-to-end demonstration view) on top of the built-ins.
    assert "steps" in view_ids
    views = {v["id"]: v for v in result["views"]}
    assert views["visual"].get("printable") is not True
    assert views["rendered"]["printable"] is True
    assert views["rendered"]["print_profile"] == "document"
    assert views["rendered"]["render_runtime"] == "kpress"
    assert views["source"]["printable"] is True
    assert views["source"]["print_profile"] == "source"


def test_plain_markdown_resolves_via_markdown_plugin(tmp_path: Path) -> None:
    """A .md file with no frontmatter resolves to 'markdown' via the
    builtin markdown plugin manifest.
    """
    server._set_root_dir(tmp_path)  # noqa: SLF001
    md = tmp_path / "plain.md"
    md.write_text("# heading\n\nbody only.\n")
    result = _api_file("plain.md")
    assert result["kind"] == "markdown"
    view_ids = [v["id"] for v in result["views"]]
    # rendered + source come from both the legacy VIEW_REGISTRY and the
    # plugin manifest — should appear once each.
    assert view_ids.count("rendered") == 1
    assert view_ids.count("source") == 1
    views = {v["id"]: v for v in result["views"]}
    assert views["rendered"]["printable"] is True
    assert views["rendered"]["print_profile"] == "document"
    assert views["rendered"]["render_runtime"] == "kpress"
    assert views["source"]["printable"] is True
    assert views["source"]["print_profile"] == "source"


def test_plain_text_source_view_is_printable(tmp_path: Path) -> None:
    server._set_root_dir(tmp_path)  # noqa: SLF001
    txt = tmp_path / "notes.txt"
    txt.write_text("plain text\n")
    result = _api_file("notes.txt")
    assert result["kind"] == "text"
    assert result["views"] == [
        {
            "id": "source",
            "label": "Source",
            "default": True,
            "container_class": "content-body metabrowser-source-host",
            "printable": True,
            "print_profile": "source",
            "render_runtime": "client",
        }
    ]


def test_unknown_jsonl_gets_default_log_view(tmp_path: Path) -> None:
    """Unknown JSONL still needs an initial visible tab in the browser."""
    server._set_root_dir(tmp_path)  # noqa: SLF001
    log = tmp_path / "agent.jsonl"
    log.write_text('{"type":"thread.started","thread_id":"abc"}\n')
    result = _api_file("agent.jsonl")
    assert result["kind"] == "unknown-jsonl"
    assert [v["id"] for v in result["views"] if v.get("default")] == ["log"]
