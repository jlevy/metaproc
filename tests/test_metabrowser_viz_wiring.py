"""Smoke tests confirming the Visual tab is wired end-to-end.

Covers the plumbing between the plugin manifest, visualization renderer, consumer view
runtime, and plugin view registration. Actual visual output is exercised by the manual
visual review gate.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from metabrowser import server as proc_browser

from metaproc.metabrowser_plugin import plugin_dir as metaproc_plugin_dir
from metaproc.metabrowser_plugin import sidekick
from metaproc.models.authored import IOSpec
from metaproc.models.viz import (
    AcceptedOutputProjection,
    DepDetails,
    FanOutDetails,
    InputSpec,
    NodeProgress,
    OutputSpec,
    ProcessHeader,
    ProgressSnapshot,
    RuntimeTaskProjection,
    StepDetails,
    TaskKeyProjection,
    TaskOutputProjection,
    UnacceptedOutputProjection,
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


def _plugin_asset_config(tmp_path: Path) -> dict[str, list[dict[str, Any]]]:
    """The kind-to-assets map the index page hands the plugin host."""
    proc_browser._set_root_dir(tmp_path)  # noqa: SLF001
    resp = asyncio.run(proc_browser.index(Mock()))
    body = bytes(resp.body).decode("utf-8")
    match = re.search(r"configureAssets\((.*?)\);</script>", body, re.DOTALL)
    assert match, "index page must configure plugin assets for the plugin host"
    return json.loads(match.group(1))


def test_the_plugin_declares_its_assets_for_every_kind_it_owns(tmp_path: Path) -> None:
    """Metabrowser 0.9 stopped putting plugin assets in the page eagerly.

    Assets are declared per kind and the host loads a descriptor the first time
    `app.js` selects a kind that claims it, so the old assertion — that the bare index
    page carries a `<link>` for viz.css — now describes a shell that no longer exists.
    What has to stay true is that every metaproc kind can still reach the metaproc
    renderers, which is what this checks instead.
    """
    config = _plugin_asset_config(tmp_path)
    for kind in ("process-spec", "resource-report", "runpool-log", "process-log"):
        descriptors = config.get(kind, [])
        assert any(d["name"] == "metaproc" for d in descriptors), (
            f"kind {kind!r} owns metaproc views but declares no metaproc assets"
        )


def test_the_layout_engine_loads_before_the_visualization_code(tmp_path: Path) -> None:
    """ELK before viz.js before domain_views.js, then the entry module.

    `viz.js` reads ELK at load time, so this order is a real dependency rather than a
    preference. The host preserves manifest order for classic scripts and mounts the
    module after them.
    """
    config = _plugin_asset_config(tmp_path)
    descriptor = next(d for d in config["process-spec"] if d["name"] == "metaproc")
    assert descriptor["scripts"] == [
        "/plugin-static/metaproc/elk.bundled.js",
        "/plugin-static/metaproc/viz.js",
        "/plugin-static/metaproc/domain_views.js",
    ]
    assert descriptor["module"] == "/plugin-static/metaproc/index.js"
    assert "/plugin-static/metaproc/viz.css" in descriptor["styles"]


def test_the_declared_assets_exist_and_carry_what_they_promise(tmp_path: Path) -> None:
    """A descriptor naming a file proves nothing on its own; the files back it."""
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
        process_inputs={
            "roster": InputSpec(
                name="roster",
                path=root + "/p/data/roster.md",
                as_type="path",
                default=root + "/p/data/default-roster.md",
            )
        },
        process_outputs={
            "report": OutputSpec(
                name="report",
                path=root + "/p/out/report.md",
                as_type="path",
                template=root + "/p/templates/report.md",
            ),
            "summary": OutputSpec(name="summary", ref="s.report", as_type="path"),
        },
    )
    step = StepDetails(
        step_id="s",
        mode="agent",
        process_schema_token="metaproc:ProcessSpec/0.1",
        source_path=root + "/p/process.md",
        uses_path=root + "/p/child/process.md",
        prompt_paths=[root + "/p/prompts/a.md", root + "/p/prompts/b.md"],
        output_root=root + "/p/out",
        inputs={"source": IOSpec(path=root + "/p/data/source.md")},
        outputs={
            "report": IOSpec(
                path=root + "/p/out/report.md",
                template=root + "/p/templates/report.md",
            )
        },
        fan_out=FanOutDetails(
            over="roster",
            source=root + "/p/data/roster.md",
        ),
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
        task_projection=TaskOutputProjection(
            run_dir=root + "/runs/2026-04-23",
            tasks=[
                RuntimeTaskProjection(
                    key=TaskKeyProjection(step_id="s"),
                    state="completed",
                    accepted_outputs=[
                        AcceptedOutputProjection(
                            name="report",
                            path=root + "/runs/2026-04-23/report.md",
                            recorded_path=root + "/runs/2026-04-23/report.md",
                            declaration=IOSpec(path="{{run.dir}}/report.md"),
                        )
                    ],
                    unaccepted_outputs=[
                        UnacceptedOutputProjection(
                            name="draft",
                            path=root + "/runs/2026-04-23/draft.md",
                            recorded_path=root + "/runs/2026-04-23/draft.md",
                            reason="result-not-validated",
                        ),
                        UnacceptedOutputProjection(
                            name="external",
                            recorded_path="/external/report.md",
                            reason="external",
                        ),
                    ],
                )
            ],
        ),
    )

    sidekick._relativize_viz_paths(model)  # noqa: SLF001

    assert model.root_process == "p/process.md"
    assert model.header.source_path == "p/process.md"
    assert model.header.process_inputs["roster"].path == "p/data/roster.md"
    assert model.header.process_inputs["roster"].default == "p/data/default-roster.md"
    assert model.header.process_outputs["report"].path == "p/out/report.md"
    assert model.header.process_outputs["report"].template == "p/templates/report.md"
    assert model.header.process_outputs["summary"].ref == "s.report"
    assert model.nodes[0].path == "p/s.md"
    assert model.nodes[0].step is not None
    assert model.nodes[0].step.source_path == "p/process.md"
    assert model.nodes[0].step.uses_path == "p/child/process.md"
    assert model.nodes[0].step.prompt_paths == ["p/prompts/a.md", "p/prompts/b.md"]
    assert model.nodes[0].step.output_root == "p/out"
    assert model.nodes[0].step.inputs["source"].path == "p/data/source.md"
    assert model.nodes[0].step.outputs["report"].path == "p/out/report.md"
    assert model.nodes[0].step.outputs["report"].template == "p/templates/report.md"
    assert model.nodes[0].step.fan_out is not None
    assert model.nodes[0].step.fan_out.source == "p/data/roster.md"
    assert model.nodes[1].dep is not None
    assert model.nodes[1].dep.path == "p/data/d.yaml"
    assert model.nodes[1].dep.source_path == "p/process.md"
    assert model.nodes[2].process is not None
    assert model.nodes[2].process.source_path == "p/process.md"
    assert model.nodes[2].process.process_inputs["roster"].path == "p/data/roster.md"
    assert model.nodes[2].process.process_outputs["report"].path == "p/out/report.md"
    assert model.progress is not None
    assert model.progress.run_dir == "runs/2026-04-23"
    assert model.task_projection is not None
    assert model.task_projection.run_dir == "runs/2026-04-23"
    assert model.task_projection.tasks[0].accepted_outputs[0].path == ("runs/2026-04-23/report.md")
    assert model.task_projection.tasks[0].unaccepted_outputs[0].path == ("runs/2026-04-23/draft.md")
    assert model.task_projection.tasks[0].unaccepted_outputs[1].path is None
