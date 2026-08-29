"""Unit test for the pure-JS ``buildElkInput`` in viz.js.

Shells out to ``node`` so we can exercise the renderer-side compound graph
builder without spinning up a real browser. The test supplies a small
``VizModel`` shape and asserts that the ELK input graph reflects the
expected parent/child structure.
"""

from __future__ import annotations

import importlib.resources
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from metaproc.metabrowser_plugin import plugin_dir
from metaproc.viz import build_viz_model
from metaproc.viz_loader import load_plan_bundle
from tests.test_viz_project import _build_synthetic_bundle

VIZ_JS = plugin_dir() / "viz.js"
ICONS_JS = Path(str(importlib.resources.files("metabrowser") / "static" / "icons.js"))


def _run_node(script: str) -> Any:
    """Evaluate ``script`` with ``window`` stubbed; return ``window.__OUT__`` as JSON.

    Loads the shared icon registry (icons.js) before viz.js so
    ``window.MetabrowserIcons`` is available to the same renderer code the
    browser would run.
    """
    bootstrap = (
        "const window = { document: null, requestAnimationFrame: () => {} };\n"
        f"{ICONS_JS.read_text()}\n"
        f"{VIZ_JS.read_text()}\n"
        f"{script}\n"
        "process.stdout.write(JSON.stringify(window.__OUT__));\n"
    )
    result = subprocess.run(
        ["node", "-e", bootstrap],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        msg = f"node failed: {result.stderr}"
        raise AssertionError(msg)
    return json.loads(result.stdout)


@pytest.fixture(autouse=True)
def _require_node() -> None:
    if shutil.which("node") is None:
        pytest.skip("node runtime not available on PATH")


def test_build_elk_input_groups_children_by_parent() -> None:
    viz = {
        "nodes": [
            {"id": "process:root", "kind": "process", "label": "root", "parent": None},
            {"id": "a", "kind": "step", "label": "a", "parent": "process:root"},
            {"id": "b", "kind": "step", "label": "b", "parent": "process:root"},
            {"id": "composite", "kind": "step", "label": "composite", "parent": "process:root"},
            {"id": "process:child", "kind": "process", "label": "child", "parent": "composite"},
            {"id": "child::leaf", "kind": "step", "label": "leaf", "parent": "process:child"},
        ],
        "edges": [
            {"source": "a", "target": "b", "kind": "needs"},
            {"source": "b", "target": "composite", "kind": "needs"},
            {"source": "child::leaf", "target": "a", "kind": "needs"},
        ],
    }
    script = (
        "const viz = " + json.dumps(viz) + ";\n"
        "window.__OUT__ = window.MetaprocViz.buildElkInput(viz);\n"
    )
    elk_input = _run_node(script)

    # Root children: only the top-level process node.
    assert [c["id"] for c in elk_input["children"]] == ["process:root"]
    root_process = elk_input["children"][0]

    # process:root contains its three siblings (a, b, composite).
    assert sorted(c["id"] for c in root_process["children"]) == ["a", "b", "composite"]

    # composite contains the child process node (nested one level deeper).
    composite = next(c for c in root_process["children"] if c["id"] == "composite")
    assert [c["id"] for c in composite["children"]] == ["process:child"]

    # Child process contains the leaf step.
    child = composite["children"][0]
    assert [c["id"] for c in child["children"]] == ["child::leaf"]

    # Leaves carry dimensions; containers don't.
    a_node = next(c for c in root_process["children"] if c["id"] == "a")
    assert a_node["width"] > 0
    assert a_node["height"] > 0
    assert "children" not in a_node
    assert "width" not in root_process

    # Cross-hierarchy edge survives (ELK's hierarchyHandling=INCLUDE_CHILDREN
    # handles the routing). All edges are at the graph root.
    edge_ids = sorted(e["id"] for e in elk_input["edges"])
    assert len(edge_ids) == 3
    assert all(e["sources"] and e["targets"] for e in elk_input["edges"])

    # Layout options carry the Phase 2 defaults.
    opts = elk_input["layoutOptions"]
    assert opts["elk.algorithm"] == "layered"
    assert opts["elk.direction"] == "DOWN"
    assert opts["elk.hierarchyHandling"] == "INCLUDE_CHILDREN"


def test_build_elk_input_collapses_non_expanded_process_nodes() -> None:
    # A two-level tree: root → composite-step → child-process → leaf.
    # With no expanded set, the child process should collapse to a leaf with
    # no descendants, and edges that cross into the collapsed subtree are dropped.
    viz = {
        "root_process_node": "process:root",
        "nodes": [
            {"id": "process:root", "kind": "process", "label": "root", "parent": None},
            {"id": "composite", "kind": "step", "label": "composite", "parent": "process:root"},
            {"id": "process:child", "kind": "process", "label": "child", "parent": "composite"},
            {"id": "child::leaf", "kind": "step", "label": "leaf", "parent": "process:child"},
        ],
        "edges": [
            {"source": "composite", "target": "child::leaf", "kind": "needs"},
        ],
    }

    # expanded = null → all expanded (regression check)
    all_expanded_script = (
        "const viz = " + json.dumps(viz) + ";\n"
        "window.__OUT__ = window.MetaprocViz.buildElkInput(viz);\n"
    )
    all_expanded = _run_node(all_expanded_script)
    composite = all_expanded["children"][0]["children"][0]
    assert composite["children"][0]["id"] == "process:child"
    assert "children" in composite["children"][0]  # leaf visible
    assert len(all_expanded["edges"]) == 1

    # expanded = empty set → child process collapses to a leaf; leaf+edge hidden
    collapsed_script = (
        "const viz = " + json.dumps(viz) + ";\n"
        "window.__OUT__ = window.MetaprocViz.buildElkInput(viz,"
        " { expanded: new Set() });\n"
    )
    collapsed = _run_node(collapsed_script)
    composite_c = collapsed["children"][0]["children"][0]
    assert composite_c["children"][0]["id"] == "process:child"
    assert "children" not in composite_c["children"][0]  # collapsed — no descendants
    # The edge referenced "child::leaf" which is no longer emitted → dropped.
    assert collapsed["edges"] == []


def test_build_elk_input_applies_per_node_dims() -> None:
    viz = {
        "nodes": [
            {"id": "process:root", "kind": "process", "label": "root", "parent": None},
            {"id": "a", "kind": "step", "label": "a", "parent": "process:root"},
            {"id": "b", "kind": "step", "label": "b", "parent": "process:root"},
        ],
        "edges": [],
    }
    script = (
        "const viz = " + json.dumps(viz) + ";\n"
        "window.__OUT__ = window.MetaprocViz.buildElkInput(viz, {\n"
        "  perNodeDims: { a: { width: 300, height: 120 } }\n"
        "});\n"
    )
    out = _run_node(script)
    root = out["children"][0]
    a_node = next(c for c in root["children"] if c["id"] == "a")
    b_node = next(c for c in root["children"] if c["id"] == "b")
    assert a_node["width"] == 300
    assert a_node["height"] == 120
    # Unoverridden node keeps the default.
    assert b_node["width"] == 240
    assert b_node["height"] == 86


def test_filter_viz_by_planes_hides_dep_nodes_and_their_edges() -> None:
    viz = {
        "root_process_node": "process:root",
        "nodes": [
            {"id": "process:root", "kind": "process", "label": "root", "parent": None},
            {"id": "predict", "kind": "step", "label": "predict", "parent": "process:root"},
            {"id": "retro", "kind": "step", "label": "retro", "parent": "process:root"},
            {"id": "dep:tickers", "kind": "dep", "label": "tickers", "parent": "process:root"},
        ],
        "edges": [
            {"source": "predict", "target": "retro", "kind": "needs"},
            {"source": "predict", "target": "dep:tickers", "kind": "produces"},
            {"source": "retro", "target": "dep:tickers", "kind": "consumes"},
        ],
    }
    script_only_steps = (
        "const viz = " + json.dumps(viz) + ";\n"
        "window.__OUT__ = window.MetaprocViz.filterVizByPlanes(viz,\n"
        "  { steps: true, artifacts: false, files: false });\n"
    )
    only_steps = _run_node(script_only_steps)
    kinds = sorted(n["kind"] for n in only_steps["nodes"])
    assert kinds == ["process", "step", "step"]
    edge_kinds = sorted(e["kind"] for e in only_steps["edges"])
    assert edge_kinds == ["needs"]

    script_artifacts_on = (
        "const viz = " + json.dumps(viz) + ";\n"
        "window.__OUT__ = window.MetaprocViz.filterVizByPlanes(viz,\n"
        "  { steps: true, artifacts: true, files: false });\n"
    )
    with_arts = _run_node(script_artifacts_on)
    assert any(n["kind"] == "dep" for n in with_arts["nodes"])
    edge_kinds2 = sorted(e["kind"] for e in with_arts["edges"])
    assert edge_kinds2 == ["consumes", "needs", "produces"]


def test_build_elk_input_override_layout_options() -> None:
    viz = {
        "nodes": [{"id": "process:root", "kind": "process", "label": "r", "parent": None}],
        "edges": [],
    }
    script = (
        "const viz = " + json.dumps(viz) + ";\n"
        "window.__OUT__ = window.MetaprocViz.buildElkInput(viz,"
        " { layoutOptions: { 'elk.direction': 'RIGHT' } });\n"
    )
    elk_input = _run_node(script)
    assert elk_input["layoutOptions"]["elk.direction"] == "RIGHT"
    # Unrelated options preserved.
    assert elk_input["layoutOptions"]["elk.algorithm"] == "layered"


def test_filter_viz_artifacts_only_reparents_dep_under_surviving_ancestor() -> None:
    """Regression: Steps=false, Artifacts=true must not produce a blank graph.

    Caught by review: every dep node sits under a composite
    step which is filtered out when Steps is off. The filter now reparents
    survivors up to the nearest surviving ancestor (here: the root process
    node) so ELK still builds a compound graph with the dep visible.
    """
    viz = {
        "root_process_node": "process:root",
        "nodes": [
            {"id": "process:root", "kind": "process", "label": "root", "parent": None},
            {"id": "composite", "kind": "step", "label": "composite", "parent": "process:root"},
            {"id": "process:child", "kind": "process", "label": "child", "parent": "composite"},
            {"id": "leaf", "kind": "step", "label": "leaf", "parent": "process:child"},
            {"id": "dep:tickers", "kind": "dep", "label": "tickers", "parent": "process:child"},
        ],
        "edges": [
            {"source": "leaf", "target": "dep:tickers", "kind": "produces"},
        ],
    }
    script = (
        "const viz = " + json.dumps(viz) + ";\n"
        "const filtered = window.MetaprocViz.filterVizByPlanes(viz,"
        " { steps: false, artifacts: true, files: false });\n"
        "window.__OUT__ = {\n"
        "  filtered: filtered,\n"
        "  elk: window.MetaprocViz.buildElkInput(filtered)\n"
        "};\n"
    )
    out = _run_node(script)

    # The dep survived. Its container (process:child) survived too because
    # it has a surviving descendant, but its own parent — the composite step
    # — was filtered out, so process:child gets reparented up to process:root.
    dep = next(n for n in out["filtered"]["nodes"] if n["id"] == "dep:tickers")
    assert dep["parent"] == "process:child"

    child_container = next(n for n in out["filtered"]["nodes"] if n["id"] == "process:child")
    assert child_container["parent"] == "process:root"

    # ELK builds a connected compound graph: root → child-process → dep.
    root_elk = out["elk"]["children"][0]
    assert root_elk["id"] == "process:root"
    child_elk = next(c for c in root_elk["children"] if c["id"] == "process:child")
    assert any(c["id"] == "dep:tickers" for c in child_elk["children"])


def test_build_elk_input_scales_to_a_200_step_composite_tree() -> None:
    """Smoke test: a 200-step, 6-deep composite tree laying out inside a second.

    The layout engine itself runs in the browser; here we just ensure the
    compound-graph builder (which runs every time ELK is called) finishes in
    a fraction of that budget on the CLI — it's a pure node graph walk.
    """
    # Build a balanced tree: 6 levels deep, fanout so total leaf nodes ~= 200.
    nodes = [{"id": "process:root", "kind": "process", "label": "root", "parent": None}]

    def add_subtree(parent_id: str, depth: int, label_prefix: str) -> None:
        if depth == 0:
            return
        for i in range(3):  # 3 children per level → 3^6 ≈ 729 leaves, plenty
            step_id = f"{label_prefix}_s{i}"
            nodes.append({"id": step_id, "kind": "step", "label": step_id, "parent": parent_id})
            if depth > 1:
                proc_id = f"process:{step_id}"
                nodes.append(
                    {"id": proc_id, "kind": "process", "label": proc_id, "parent": step_id}
                )
                add_subtree(proc_id, depth - 1, step_id)

    add_subtree("process:root", 6, "L")
    # Trim to 200 step nodes; grab the first 200 steps + every process ancestor + root.
    step_ids = [n["id"] for n in nodes if n["kind"] == "step"][:200]
    kept = set(step_ids) | {"process:root"}
    # Include every process ancestor required for containment.
    by_id = {n["id"]: n for n in nodes}
    for sid in list(step_ids):
        cur = by_id[sid].get("parent")
        while cur and cur not in kept:
            kept.add(cur)
            cur = by_id[cur].get("parent")
    viz = {
        "root_process_node": "process:root",
        "nodes": [n for n in nodes if n["id"] in kept],
        "edges": [],
    }

    t = time.perf_counter()
    script = (
        "const viz = " + json.dumps(viz) + ";\n"
        "window.__OUT__ = window.MetaprocViz.buildElkInput(viz);\n"
    )
    out = _run_node(script)
    elapsed = time.perf_counter() - t

    # Well-formed tree
    assert out["children"], "root must have at least the root process node"
    assert elapsed < 5.0, f"buildElkInput too slow on 200 steps: {elapsed:.2f}s"


def test_build_elk_input_end_to_end_from_plan_projection() -> None:
    """End-to-end: Plan → VizModel → buildElkInput for the synthetic fixture.

    Pins the shape of the Phase 2 contract: every process node becomes an
    ELK container, step/dep leaves are parented inside, and cross-hierarchy
    edges survive. The specific ids/counts are intentionally tested so that
    changes to projection or builder surface here.
    """

    tmp = Path("/tmp") / "viz_e2e_fixture"
    tmp.mkdir(parents=True, exist_ok=True)
    process_path = _build_synthetic_bundle(tmp)
    bundle = load_plan_bundle(process_path, params={"CUTOFF_DATE": "2026-04-21"})
    viz = build_viz_model(bundle)
    viz_json = viz.model_dump(by_alias=True)

    script = (
        "const viz = " + json.dumps(viz_json) + ";\n"
        "window.__OUT__ = window.MetaprocViz.buildElkInput(viz);\n"
    )
    elk = _run_node(script)

    # Exactly one top-level ELK child: the root process container.
    assert [c["id"] for c in elk["children"]] == ["process:root"]
    root = elk["children"][0]
    # Root contains the three steps + three deps.
    child_ids = sorted(c["id"] for c in root["children"])
    assert "scaffold" in child_ids
    assert "predict" in child_ids
    assert "retro" in child_ids
    assert "dep:tickers" in child_ids
    assert "dep:predict_runbook" in child_ids
    assert "dep:predictions_out" in child_ids

    # Layout options are the Phase-2 defaults.
    assert elk["layoutOptions"]["elk.hierarchyHandling"] == "INCLUDE_CHILDREN"
    assert elk["layoutOptions"]["elk.direction"] == "DOWN"

    # Edges include the full produces/consumes/needs set; no contains.
    kinds = sorted({e["kind"] for e in elk["edges"]})
    assert "needs" in kinds
    assert "produces" in kinds
    assert "consumes" in kinds
    assert "contains" not in kinds


def test_render_side_panel_includes_step_adapter_and_io_tables() -> None:
    step_node = {
        "id": "run_predict",
        "kind": "step",
        "label": "run_predict",
        "parent": "process:root",
        "step": {
            "step_id": "run_predict",
            "mode": "composite",
            "description": "Predict per ticker.",
            "needs": ["scaffold"],
            "adapter": {"type": "pi-cli", "model": "sonnet", "tools": ["bash"]},
            "fan_out": {
                "over": "deps.tickers",
                "bind": "ticker",
                "source": "data/tickers.yaml",
                "item_count": 3,
                "filtered_count": 2,
                "bind_fields": [],
                "batch_size": None,
                "align": "same_key",
                "max_concurrency": 4,
                "retry": {
                    "max_retries": 3,
                    "initial_backoff_s": 2.0,
                    "backoff_multiplier": 2.0,
                    "max_backoff_s": 30.0,
                },
            },
            "inputs_summary": [{"name": "tickers", "as_type": "list<string>", "produced_by": None}],
            "outputs_summary": [{"name": "prediction", "as_type": None, "produced_by": None}],
            "inputs": {"tickers": {"path": "data/tickers.yaml"}},
            "outputs": {"prediction": {"path": "{{run.dir}}/out.md"}},
            "prompt_paths": ["prompts/predict.runbook.md"],
            "handler": None,
            "command": None,
            "uses_path": "predict/predict.process.md",
            "prompt_prefix": None,
            "output_root": None,
            "with_": {"cutoff_date": "{{inputs.cutoff_date}}", "form_version": "3"},
            "env": {},
            "max_budget_usd": None,
            "token_budget": None,
            "reuse_policy": None,
            "variant": None,
            "resources": {"memory": "large"},
            "on_failure": "continue",
            "execution_profile": "pi-fast",
            "artifact_namespace": "pi-fast",
        },
    }
    script = (
        "const node = " + json.dumps(step_node) + ";\n"
        "window.__OUT__ = window.MetaprocViz.renderSidePanel(node);\n"
    )
    html = _run_node(script)
    assert isinstance(html, str)
    # Panel header shows a kind-icon SVG (step) instead of a text badge.
    assert "viz-kind-icon-step" in html
    assert "Predict per ticker" in html
    # Adapter section renders its fields.
    assert "pi-cli" in html
    assert "sonnet" in html
    # Fan-out section present, including retry policy (retry-policy coverage gap).
    assert "deps.tickers" in html
    assert "data/tickers.yaml" in html
    assert "filtered_count" in html
    assert "same_key" in html
    assert "max_concurrency" in html
    assert "max=3" in html
    assert "init=2s" in html
    assert "pi-fast" in html
    assert "continue" in html
    assert "memory" in html
    assert "large" in html
    # IO tables present and now carry the raw authored path column.
    assert "tickers" in html
    assert "prediction" in html
    assert "data/tickers.yaml" in html
    # Prompt paths rendered as links.
    assert "predict.runbook.md" in html
    # `with:` parameter bindings visible (composite step case).
    assert "With (parameter bindings)" in html
    assert "cutoff_date" in html
    assert "form_version" in html


def test_render_side_panel_process_shows_defaults_and_schemas_and_body() -> None:
    process_node = {
        "id": "process:root",
        "kind": "process",
        "label": "example_plugin",
        "parent": None,
        "process": {
            "name": "example_plugin",
            "description": "End-to-end earnings lifecycle.",
            "process_schema_token": "metaproc:ProcessSpec/0.1",
            "source_path": "example_plugin/main.process.md",
            "process_inputs": {
                "roster": {
                    "name": "roster",
                    "path": "data/roster.md",
                    "param": None,
                    "as_type": "path",
                    "parse": {"format": "frontmatter-md", "extract": "items"},
                    "required": True,
                    "default": "data/default-roster.md",
                    "description": "Mapped work roster.",
                }
            },
            "process_outputs": {
                "report": {
                    "name": "report",
                    "ref": "work.report",
                    "as_type": "path",
                    "format": "frontmatter-md",
                    "template": "templates/report.md",
                    "condition": "RUN_MODE == smoke",
                    "description": "Accepted report.",
                }
            },
            "defaults": {
                "default_adapter": "main",
                "default_execution_profile": "pi-fast",
                "recommended_execution_profiles": ["pi-fast", "gemini-flash"],
                "reuse_policy": "exact_inputs",
                "adapters": {
                    "main": {"type": "pi-cli", "model": "sonnet", "provider": "anthropic"}
                },
                "retry": {
                    "max_retries": 5,
                    "initial_backoff_s": 1.0,
                    "backoff_multiplier": 2.0,
                    "max_backoff_s": 60.0,
                },
            },
            "registered_schemas": ["metaproc:Prediction/0.1", "metaproc:Items/0.2"],
            "body_markdown": "## About\n\nLifecycle orchestrator.",
        },
    }
    script = (
        "const node = " + json.dumps(process_node) + ";\n"
        "window.__OUT__ = window.MetaprocViz.renderSidePanel(node);\n"
    )
    html = _run_node(script)
    assert isinstance(html, str)
    assert "viz-kind-icon-process" in html
    assert "Defaults" in html
    assert "main" in html
    assert "pi-fast" in html
    assert "gemini-flash" in html
    assert "exact_inputs" in html
    assert "max=5" in html  # defaults.retry
    assert "Operator inputs" in html
    assert "data/roster.md" in html
    assert "frontmatter-md:items" in html
    assert "data/default-roster.md" in html
    assert "Public outputs" in html
    assert "work.report" in html
    assert 'data-path="work.report"' not in html
    assert "frontmatter-md" in html
    assert "templates/report.md" in html
    assert "RUN_MODE == smoke" in html
    assert "metaproc:Prediction/0.1" in html
    assert "metaproc:Items/0.2" in html
    # Body markdown in a collapsible details block, not dumped as plain text.
    assert "<details" in html
    assert "Lifecycle orchestrator" in html
