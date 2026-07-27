"""Tests for the static SVG / HTML renderers and the ``plan --format`` CLI flag."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.models.authored import ProcessSpec
from metaproc.models.plan import Plan
from metaproc.viz import build_viz_model, render_html, render_svg
from metaproc.viz.layout import NODE_HEIGHT, NODE_WIDTH, layout_viz_model
from metaproc.viz.project import PlanBundle
from metaproc.viz.render_svg import parse_svg_metadata
from metaproc.viz_loader import load_plan_bundle

_PROCESS = """\
---
process:
  name: synthetic
  description: Contract round-trip
  inputs:
    cutoff:
      param: CUTOFF
      as: string
  deps:
    items:
      path: data/items.yaml
      as: list<string>
  steps:
    - id: build
      mode: code
      handler: metaproc.code_steps.build
    - id: predict
      mode: agent
      needs:
        - build
      output_root: runs/predict
      outputs:
        prediction:
          path: prediction.md
---

# Body.
"""


def _write_process(tmp_path: Path) -> Path:
    process = tmp_path / "syn" / "test.process.md"
    process.parent.mkdir(parents=True, exist_ok=True)
    process.write_text(_PROCESS)
    (tmp_path / "syn" / "data").mkdir(exist_ok=True)
    (tmp_path / "syn" / "data" / "items.yaml").write_text("---\nitems: []\n---\n")
    return process


# ── Layout ──────────────────────────────────────────────────────


def test_layout_places_every_node_and_routes_every_edge(tmp_path: Path) -> None:
    bundle = load_plan_bundle(_write_process(tmp_path), params={"CUTOFF": "today"})
    viz = build_viz_model(bundle)
    laid = layout_viz_model(viz)

    # Every VizModel node gets laid out.
    assert {n.id for n in laid.nodes} == {n.id for n in viz.nodes}
    # Every edge gets waypoints (start, mid-1, mid-2, end).
    for edge in laid.edges:
        assert len(edge.waypoints) >= 2
    # Overall canvas at least fits one node plus padding.
    assert laid.width >= NODE_WIDTH
    assert laid.height >= NODE_HEIGHT


def test_layout_handles_empty_model() -> None:
    viz = build_viz_model(
        PlanBundle(
            plan=Plan(process="x/test.process.md"), spec=ProcessSpec(name="x"), source_path="x"
        )
    )
    laid = layout_viz_model(viz)
    # Even an empty plan carries the root process node; layout places it.
    assert len(laid.nodes) == 1
    assert laid.nodes[0].id == "process:root"
    assert laid.edges == []


# ── SVG ─────────────────────────────────────────────────────────


def test_render_svg_emits_well_formed_document_with_metadata(tmp_path: Path) -> None:
    bundle = load_plan_bundle(_write_process(tmp_path), params={"CUTOFF": "today"})
    viz = build_viz_model(bundle)

    svg = render_svg(viz)
    assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg"')
    assert svg.rstrip().endswith("</svg>")
    assert "<metadata>" in svg
    assert "metaproc:vizmodel" in svg


def test_render_svg_round_trips_to_vizmodel(tmp_path: Path) -> None:
    bundle = load_plan_bundle(_write_process(tmp_path), params={"CUTOFF": "today"})
    viz = build_viz_model(bundle)

    svg = render_svg(viz)
    restored = parse_svg_metadata(svg)
    assert restored == viz


def test_render_svg_metadata_handles_xml_special_characters(tmp_path: Path) -> None:
    bundle = load_plan_bundle(_write_process(tmp_path), params={"CUTOFF": "today"})
    viz = build_viz_model(bundle)
    special = viz.nodes[0].model_copy(update={"label": "A < B & C"})
    viz = viz.model_copy(update={"nodes": [special, *viz.nodes[1:]]})

    svg = render_svg(viz)

    assert "<![CDATA[" in svg
    assert parse_svg_metadata(svg) == viz


def test_parse_svg_metadata_fails_clearly_on_missing_block() -> None:
    with pytest.raises(ValueError, match="does not contain"):
        parse_svg_metadata("<svg></svg>")


# ── HTML ────────────────────────────────────────────────────────


def test_render_html_is_self_contained(tmp_path: Path) -> None:
    bundle = load_plan_bundle(_write_process(tmp_path), params={"CUTOFF": "today"})
    viz = build_viz_model(bundle)

    html = render_html(viz)
    assert html.startswith("<!doctype html>")
    assert "<svg" in html
    # No network fetches (no <script src=> pointing outward, no <link rel=stylesheet>).
    assert 'src="http' not in html
    assert 'rel="stylesheet"' not in html


# ── CLI wiring ──────────────────────────────────────────────────


def test_plan_cli_emits_svg(tmp_path: Path) -> None:
    process = _write_process(tmp_path)
    output = tmp_path / "out.svg"

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "plan",
            str(process),
            "--var",
            "CUTOFF=today",
            "--format",
            "svg",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    svg_text = output.read_text()
    assert svg_text.startswith('<svg xmlns="http://www.w3.org/2000/svg"')
    # Round-trip: the embedded VizModel survives disk I/O.
    restored = parse_svg_metadata(svg_text)
    assert restored.header.name == "synthetic"


def test_plan_cli_rejects_unknown_format(tmp_path: Path) -> None:
    process = _write_process(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["plan", str(process.parent), "--format", "pdf"],
    )
    assert result.exit_code != 0
    message = str(result.exception) if result.exception else result.output
    assert "--format must be one of" in message
