"""Tests for plugin-contributed :class:`NodeDecoration` merging."""

from __future__ import annotations

from metaproc.models.viz import (
    NodeDecoration,
    ProcessHeader,
    StepDetails,
    VizEdge,
    VizModel,
    VizNode,
)
from metaproc.plugins.protocol import VizDecorator
from metaproc.viz.decorators import decorate_nodes
from metaproc.viz.render_svg import render_svg


def _minimal_model() -> VizModel:
    step_one = VizNode(
        id="predict",
        kind="step",
        label="predict",
        step=StepDetails(
            step_id="predict",
            mode="agent",
            variant="opus",
            process_schema_token="metaproc:ProcessSpec/0.1",
            source_path="x/process.md",
        ),
    )
    step_two = VizNode(
        id="other",
        kind="step",
        label="other",
        step=StepDetails(
            step_id="other",
            mode="code",
            process_schema_token="metaproc:ProcessSpec/0.1",
            source_path="x/process.md",
        ),
    )
    return VizModel(
        root_process="x/process.md",
        header=ProcessHeader(
            name="x", process_schema_token="metaproc:ProcessSpec/0.1", source_path="x/process.md"
        ),
        nodes=[step_one, step_two],
        edges=[VizEdge(source="predict", target="other", kind="needs")],
        levels=[["predict"], ["other"]],
    )


def test_decorate_nodes_merges_matching_decorators() -> None:
    def predicate(node: VizNode) -> bool:
        return node.label == "predict"

    def decorate(_node: VizNode) -> NodeDecoration:
        return NodeDecoration(badge="hot", accent_token="warm", tooltip_addendum="foo")

    decorated = decorate_nodes(
        _minimal_model(),
        [VizDecorator(predicate=predicate, decorate=decorate)],
    )
    by_id = {d.node.id: d for d in decorated}
    assert by_id["predict"].decoration is not None
    assert by_id["predict"].decoration.badge == "hot"
    assert by_id["predict"].decoration.accent_token == "warm"
    assert by_id["other"].decoration is None


def test_decorate_nodes_merges_multiple_decorators_preserving_first_non_none() -> None:
    def always(_node: VizNode) -> bool:
        return True

    def deco_a(_node: VizNode) -> NodeDecoration:
        return NodeDecoration(badge="first", tooltip_addendum="one")

    def deco_b(_node: VizNode) -> NodeDecoration:
        return NodeDecoration(badge="second", accent_token="accent", tooltip_addendum="two")

    decorated = decorate_nodes(
        _minimal_model(),
        [
            VizDecorator(predicate=always, decorate=deco_a),
            VizDecorator(predicate=always, decorate=deco_b),
        ],
    )
    predict = next(d for d in decorated if d.node.id == "predict")
    assert predict.decoration is not None
    assert predict.decoration.badge == "first"  # first-wins for scalar fields
    assert predict.decoration.accent_token == "accent"  # contributed by second only
    assert predict.decoration.tooltip_addendum == "one\ntwo"  # concatenated


def test_render_svg_embeds_badge_when_decorator_matches() -> None:
    def predicate(node: VizNode) -> bool:
        return node.label == "predict"

    def decorate(_node: VizNode) -> NodeDecoration:
        return NodeDecoration(badge="v7", accent_token="predict")

    svg = render_svg(
        _minimal_model(),
        decorators=[VizDecorator(predicate=predicate, decorate=decorate)],
    )
    assert "viz-label badge" in svg
    assert ">v7<" in svg
    assert "accent-predict" in svg


def test_render_svg_without_decorators_omits_badges() -> None:
    svg = render_svg(_minimal_model())
    assert "viz-label badge" not in svg
    assert "accent-" not in svg
