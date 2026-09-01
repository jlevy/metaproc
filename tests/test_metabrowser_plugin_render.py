"""Per-view render contract tests for the metaproc plugin.

These exercise the actual JS renderers via the JSDOM shim
(``tests/dom/render_view.js``) so we can catch regressions where a
view's render body doesn't match the ``/api/file`` payload shape.
The shim is JSDOM-free (Node ``vm`` + tiny stubs) — same approach as
``test_plugin_e2e_render.py``.

Why these are here, not in the metabrowser package: the metaproc
plugin is a separate package; metabrowser's e2e shim handles the
contract layer (every (kind, viewId) registers), but content-level
assertions belong with the plugin.
"""

from __future__ import annotations

import importlib.resources
import json
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest

from metaproc.metabrowser_plugin import plugin_dir
from tests.plugin_manifest_contract import manifest_contracts_json

METABROWSER_PACKAGE_ROOT = Path(str(importlib.resources.files("metabrowser")))
METAPROC_PLUGIN_ROOT = plugin_dir()
SHIM = Path(__file__).resolve().parent / "dom" / "render_metabrowser_view.js"
MANIFEST_CONTRACTS = manifest_contracts_json(
    METABROWSER_PACKAGE_ROOT,
    METAPROC_PLUGIN_ROOT,
)
NODE_SUBPROCESS_TIMEOUT_SECONDS = 30


class _AttributeCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.attributes: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del tag
        self.attributes.extend(attrs)


def _has_node() -> bool:
    return shutil.which("node") is not None


def _render(kind: str, view: str, payload: dict[str, Any]) -> str:
    if not _has_node():
        pytest.skip("node not available")
    result = subprocess.run(
        [
            "node",
            "--experimental-vm-modules",
            str(SHIM),
            str(METABROWSER_PACKAGE_ROOT),
            str(METAPROC_PLUGIN_ROOT),
            MANIFEST_CONTRACTS,
            kind,
            view,
            json.dumps(payload),
        ],
        capture_output=True,
        text=True,
        timeout=NODE_SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
    )
    assert result.returncode == 0, f"shim failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    return result.stdout


def test_resource_report_raw_pretty_prints_valid_json() -> None:
    """resource-report/raw renders ctx.raw.content as pretty-printed JSON.

    Earlier the view delegated to the agent-log raw renderer (which
    reads ``data.raw_text``); ``resources.json`` is served as text
    with ``content``, so the Raw tab came back empty. The plugin now
    has a JSON-aware ``jsonRaw`` renderer.
    """
    payload = {
        "kind": "resource-report",
        "path": "resources.json",
        "ext": ".json",
        "content": '{"schema": "metaproc.resources/0.1", "totals": {"input_tokens": 42}}',
    }
    html = _render("resource-report", "raw", payload)
    # Valid JSON should be pretty-printed (newlines + 2-space indent).
    # mb.escapeHtml converts " to &quot; so match the escaped form.
    assert "&quot;input_tokens&quot;: 42" in html
    assert "&quot;schema&quot;: &quot;metaproc.resources/0.1&quot;" in html
    # Newlines from JSON.stringify(..., null, 2) survive HTML escape.
    assert "\n  " in html, f"output should be indented; got: {html!r}"
    # Wrapped in a code block so highlight.js can pick it up.
    assert 'class="language-json"' in html
    assert "code-block" in html


def test_resource_report_raw_falls_back_to_verbatim_for_malformed_json() -> None:
    """If JSON.parse fails the renderer paints the raw bytes so the
    operator can still inspect the file."""
    payload = {
        "kind": "resource-report",
        "path": "broken.json",
        "ext": ".json",
        "content": "{this is not json}",
    }
    html = _render("resource-report", "raw", payload)
    # Verbatim content should appear (HTML-escaped).
    assert "this is not json" in html


def test_resource_report_actions_do_not_interpolate_inline_handlers() -> None:
    hostile_node_id = 'step-1" onclick="globalThis.pwned=true'
    hostile_path = '.logs/run.jsonl" onclick="globalThis.pwned=true'
    payload = {
        "kind": "resource-report",
        "path": "resources.json",
        "ext": ".json",
        "content": json.dumps(
            {
                "schema": "metaproc.resources/v1",
                "run_id": "fixture",
                "generated_at": "2026-07-16T00:00:00Z",
                "hierarchy_root": {
                    "node_id": hostile_node_id,
                    "node_type": "step",
                    "label": hostile_node_id,
                    "self_metrics": {},
                    "total_metrics": {},
                    "source_refs": [],
                    "log_summary": {"source_log_count": 1},
                    "children": [],
                },
                "source_logs": [
                    {
                        "owner_node_id": hostile_node_id,
                        "path": hostile_path,
                        "adapter": "fixture",
                        "kind": "fixture",
                        "summary": {},
                    }
                ],
                "taxonomy_rollups": {},
            }
        ),
    }

    html = _render("resource-report", "resources", payload)

    parser = _AttributeCollector()
    parser.feed(html)
    assert not {"onclick", "onchange"}.intersection(name for name, _value in parser.attributes)
    assert 'data-metaproc-action="resource-node"' in html
    assert 'data-metaproc-action="open-path"' in html
    assert "&quot; onclick=&quot;globalThis.pwned=true" in html


def test_resource_report_renders_v2_operational_visibility() -> None:
    payload = {
        "kind": "resource-report",
        "path": "resources.json",
        "ext": ".json",
        "content": json.dumps(
            {
                "schema": "metaproc.resources/v2",
                "run_id": "fixture",
                "generated_at": "2026-08-03T00:00:00Z",
                "hierarchy_root": {
                    "node_id": "fixture",
                    "node_type": "run",
                    "label": "fixture",
                    "self_metrics": {},
                    "total_metrics": {"actual_cost_usd": 1.0, "list_cost_usd": 1.5},
                    "source_refs": [],
                    "log_summary": {},
                    "children": [],
                },
                "finalization": {"state": "completed"},
                "meter_rollups": [
                    {
                        "key": {
                            "provider": "anthropic",
                            "product": "claude-code",
                            "meter": "turns",
                            "unit": "count",
                        },
                        "coverage": "measured",
                        "actual_quantity": 2,
                    }
                ],
                "coverage_gaps": [],
                "budget_evaluations": [
                    {
                        "budget": {
                            "budget_id": "bud-turns",
                            "scope": {"kind": "run"},
                        },
                        "status": "within",
                        "coverage": "measured",
                        "message": "turns=2; threshold=10",
                    }
                ],
                "source_logs": [],
                "taxonomy_rollups": {},
            }
        ),
    }

    html = _render("resource-report", "resources", payload)

    assert "List estimate" in html
    assert "Outcome" in html
    assert "Provider meters" in html
    assert "anthropic" in html
    assert "Resource budgets" in html
    assert "bud-turns" in html


def test_resource_report_distinguishes_unmeasured_from_zero() -> None:
    payload = {
        "kind": "resource-report",
        "path": "resources.json",
        "ext": ".json",
        "content": json.dumps(
            {
                "schema": "metaproc.resources/v2",
                "run_id": "fixture",
                "generated_at": "2026-08-03T00:00:00Z",
                "hierarchy_root": {
                    "node_id": "fixture",
                    "node_type": "run",
                    "label": "fixture",
                    "self_metrics": {},
                    "total_metrics": {"actual_cost_usd": None, "list_cost_usd": 0},
                    "source_refs": [],
                    "log_summary": {},
                    "children": [],
                },
                "source_logs": [],
                "taxonomy_rollups": {},
            }
        ),
    }

    html = _render("resource-report", "resources", payload)

    assert "unmeasured" in html
    assert "$0.00" in html


def test_resource_report_does_not_present_partial_tokens_as_a_total() -> None:
    payload = {
        "kind": "resource-report",
        "path": "resources.json",
        "ext": ".json",
        "content": json.dumps(
            {
                "schema": "metaproc.resources/v2",
                "run_id": "fixture",
                "generated_at": "2026-08-03T00:00:00Z",
                "hierarchy_root": {
                    "node_id": "fixture",
                    "node_type": "run",
                    "label": "fixture",
                    "self_metrics": {},
                    "total_metrics": {
                        "input_tokens": 10,
                        "output_tokens": 2,
                        "cache_read_tokens": None,
                        "cache_write_tokens": 0,
                        "wall_time_s": 0,
                        "actual_cost_usd": 0,
                        "list_cost_usd": 0,
                        "tool_calls": 0,
                    },
                    "source_refs": [],
                    "log_summary": {},
                    "children": [],
                },
                "source_logs": [],
                "taxonomy_rollups": {},
            }
        ),
    }

    html = _render("resource-report", "resources", payload)

    assert html.count("unmeasured") >= 2


def test_structure_report_summary_renders_counts() -> None:
    payload = {
        "kind": "structure-report",
        "path": "structure-report.md",
        "ext": ".md",
        "frontmatter": {
            "structure_report": {
                "summary": {
                    "artifacts": 3,
                    "warnings": 1,
                    "by_status": {"soft": 1, "enforced": 2},
                    "by_stage": {"frontmatter": 1, "validated_frontmatter": 2},
                    "by_profile": {"frontmatter-md": 3},
                },
                "warnings": [{"code": "schema-declared-without-binding", "message": "x"}],
            }
        },
    }

    html = _render("structure-report", "summary", payload)

    assert "Structure Report" in html
    assert "Artifacts" in html
    assert "Warnings" in html
    assert "validated_frontmatter" in html
    assert "schema-declared-without-binding" in html


def test_structure_report_artifacts_renders_table() -> None:
    payload = {
        "kind": "structure-report",
        "path": "structure-report.md",
        "ext": ".md",
        "frontmatter": {
            "structure_report": {
                "artifacts": [
                    {
                        "id": "predict.prediction",
                        "schema": "earnings:Prediction/v1",
                        "status": "enforced",
                        "stage": "validated_frontmatter",
                        "producer_step": "predict",
                        "consumers": ["qa"],
                        "warnings": [],
                    }
                ]
            }
        },
    }

    html = _render("structure-report", "artifacts", payload)

    assert "predict.prediction" in html
    assert "earnings:Prediction/v1" in html
    assert "validated_frontmatter" in html
    assert "qa" in html
