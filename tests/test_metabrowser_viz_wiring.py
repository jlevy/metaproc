"""Smoke tests confirming the Visual tab is wired end-to-end.

Covers the plumbing between the plugin manifest, visualization renderer, consumer view
runtime, and plugin view registration. Actual visual output is exercised by the manual
visual review gate.
"""

from __future__ import annotations

import asyncio
import importlib.resources
from pathlib import Path
from unittest.mock import Mock

import pytest
from metabrowser import server as proc_browser

from metaproc.metabrowser_plugin import plugin_dir as metaproc_plugin_dir
from metaproc.metabrowser_plugin import sidekick
from metaproc.models.viz import (
    DepDetails,
    NodeProgress,
    ProcessHeader,
    ProgressSnapshot,
    StepDetails,
    VizModel,
    VizNode,
)

METABROWSER_PACKAGE_ROOT = Path(str(importlib.resources.files("metabrowser")))
METAPROC_PLUGIN_ROOT = metaproc_plugin_dir()


@pytest.fixture
def repo_root() -> Path:
    # tests/ — the standalone repository root is one parent up.
    return Path(__file__).resolve().parents[1]


def test_viz_js_exposes_render_viz_entry() -> None:
    viz_js = (METAPROC_PLUGIN_ROOT / "viz.js").read_text()
    assert "MetaprocViz" in viz_js
    assert "renderViz" in viz_js


def test_viz_css_defines_node_variants() -> None:
    viz_css = (METAPROC_PLUGIN_ROOT / "viz.css").read_text()
    assert ".viz-node-step" in viz_css
    assert ".viz-node-dep" in viz_css


def test_legacy_duplicate_viz_assets_are_absent(repo_root: Path) -> None:
    legacy_dir = repo_root / "src" / "metaproc" / "viz" / "static"
    assert not (legacy_dir / "viz.js").exists()
    assert not (legacy_dir / "viz.css").exists()


def test_metaproc_plugin_registers_visual_view() -> None:
    """Visual tab is wired through the metaproc plugin's index.js
    and domain_views.js loader, which fetches from the plugin data hook.
    MetaBrowser core must not retain domain-specific visual loading."""
    app_js = (METABROWSER_PACKAGE_ROOT / "static" / "app.js").read_text()
    plugin_js = (METAPROC_PLUGIN_ROOT / "index.js").read_text()
    domain_views_js = (METAPROC_PLUGIN_ROOT / "domain_views.js").read_text()
    # Plugin owns the registration:
    assert 'mb.registerView("process-spec", "visual"' in plugin_js
    assert "domainViews().loadVisual" in plugin_js
    assert "loadVisual" in domain_views_js
    assert 'requestPluginData("viz-model", { process: path })' in domain_views_js
    assert 'mb.fetchPluginData("metaproc", route, parameters)' in domain_views_js
    assert '"/api/plugin/metaproc/' not in domain_views_js
    assert "loadVisual" not in app_js


def test_index_page_links_viz_assets_and_layout_engine(tmp_path: Path) -> None:
    """The plugin loads its layout engine before visualization code."""

    proc_browser._set_root_dir(tmp_path)  # noqa: SLF001
    resp = asyncio.run(proc_browser.index(Mock()))
    body = bytes(resp.body).decode("utf-8")
    assert '<link rel="stylesheet" href="/plugin-static/metaproc/viz.css">' in body
    assert '<script src="/plugin-static/metaproc/viz.js"></script>' in body
    elk_tag = '<script src="/plugin-static/metaproc/elk.bundled.js"></script>'
    viz_tag = '<script src="/plugin-static/metaproc/viz.js"></script>'
    domain_views_tag = '<script src="/plugin-static/metaproc/domain_views.js"></script>'
    index_tag = '<script type="module" src="/plugin-static/metaproc/index.js"></script>'
    assert body.index(elk_tag) < body.index(viz_tag)
    assert body.index(viz_tag) < body.index(domain_views_tag)
    assert body.index(domain_views_tag) < body.index(index_tag)
    viz_css = (METAPROC_PLUGIN_ROOT / "viz.css").read_text()
    viz_js = (METAPROC_PLUGIN_ROOT / "viz.js").read_text()
    elk_js = (METAPROC_PLUGIN_ROOT / "elk.bundled.js").read_text()
    elk_license = (METAPROC_PLUGIN_ROOT / "elkjs-license.txt").read_text()
    assert ".viz-node-step" in viz_css
    assert "MetaprocViz" in viz_js
    assert "ELK" in elk_js
    assert "Eclipse Public License - v 2.0" in elk_license


# ── Path relativization (ROOT_DIR-relative paths in JSON responses) ──


def test_relativize_passes_through_none_and_empty() -> None:
    assert proc_browser._relativize(None) is None  # noqa: SLF001
    assert proc_browser._relativize("") == ""  # noqa: SLF001


def test_relativize_preserves_already_relative_path(tmp_path: Path) -> None:
    proc_browser._set_root_dir(tmp_path)  # noqa: SLF001
    assert proc_browser._relativize("process/predict.md") == "process/predict.md"  # noqa: SLF001


def test_relativize_strips_root_prefix_from_absolute_path(tmp_path: Path) -> None:
    proc_browser._set_root_dir(tmp_path)  # noqa: SLF001
    absolute = str(tmp_path / "process" / "predict.md")
    assert proc_browser._relativize(absolute) == "process/predict.md"  # noqa: SLF001


def test_relativize_preserves_absolute_path_outside_root(tmp_path: Path) -> None:
    # Safety valve: a path outside ROOT_DIR is returned as-is rather than
    # silently rewritten to `../something` (which wouldn't resolve for
    # the client).
    proc_browser._set_root_dir(tmp_path)  # noqa: SLF001
    outside = "/var/log/something.log"
    assert proc_browser._relativize(outside) == outside  # noqa: SLF001


def test_relativize_is_idempotent(tmp_path: Path) -> None:
    proc_browser._set_root_dir(tmp_path)  # noqa: SLF001
    absolute = str(tmp_path / "nested" / "file.md")
    once = proc_browser._relativize(absolute)
    twice = proc_browser._relativize(once)
    assert once == twice == "nested/file.md"


def test_relativize_viz_paths_walks_every_path_field(tmp_path: Path) -> None:

    proc_browser._set_root_dir(tmp_path)  # noqa: SLF001
    root = str(tmp_path)
    header = ProcessHeader(
        name="p",
        process_schema_token="metaproc:ProcessSpec/0.1",
        source_path=root + "/p/process.md",
    )
    step = StepDetails(
        step_id="s",
        mode="agent",
        process_schema_token="metaproc:ProcessSpec/0.1",
        source_path=root + "/p/process.md",
        uses_path=root + "/p/child/process.md",
        prompt_paths=[root + "/p/prompts/a.md", root + "/p/prompts/b.md"],
        output_root=root + "/p/out",
    )
    dep = DepDetails(
        dep_name="d",
        path=root + "/p/data/d.yaml",
        usage=["for_each.over"],
        process_schema_token="metaproc:ProcessSpec/0.1",
        source_path=root + "/p/process.md",
    )
    model = VizModel(
        root_process=root + "/p/process.md",
        header=header,
        nodes=[
            VizNode(id="s", kind="step", label="s", step=step, path=root + "/p/s.md"),
            VizNode(id="d", kind="dep", label="d", dep=dep),
            VizNode(id="p", kind="process", label="p", process=header),
        ],
        progress=ProgressSnapshot(
            run_dir=root + "/runs/2026-04-23",
            nodes={"s": NodeProgress(state="running")},
        ),
    )

    sidekick._relativize_viz_paths(model)  # noqa: SLF001

    assert model.root_process == "p/process.md"
    assert model.header.source_path == "p/process.md"
    assert model.nodes[0].path == "p/s.md"
    assert model.nodes[0].step is not None
    assert model.nodes[0].step.source_path == "p/process.md"
    assert model.nodes[0].step.uses_path == "p/child/process.md"
    assert model.nodes[0].step.prompt_paths == ["p/prompts/a.md", "p/prompts/b.md"]
    assert model.nodes[0].step.output_root == "p/out"
    assert model.nodes[1].dep is not None
    assert model.nodes[1].dep.path == "p/data/d.yaml"
    assert model.nodes[1].dep.source_path == "p/process.md"
    assert model.nodes[2].process is not None
    assert model.nodes[2].process.source_path == "p/process.md"
    assert model.progress is not None
    assert model.progress.run_dir == "runs/2026-04-23"
