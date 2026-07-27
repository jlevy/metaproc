"""Round-trip and schema-token tests for the visualization models."""

from __future__ import annotations

import pytest

from metaproc.models.authored import IOSpec, RetryPolicy
from metaproc.models.viz import (
    AdapterSummary,
    DefaultsBlock,
    DepDetails,
    FanOutDetails,
    InputSpec,
    LaidOut,
    LaidOutEdge,
    LaidOutNode,
    NodeDecoration,
    NodeProgress,
    ProcessHeader,
    ProgressSnapshot,
    StepDetails,
    VizEdge,
    VizModel,
    VizNode,
    VizViewState,
)


def _sample_step_details() -> StepDetails:
    return StepDetails.model_validate(
        {
            "step_id": "predict",
            "mode": "agent",
            "description": "Run predictions",
            "needs": ["scaffold-day"],
            "process_schema_token": "metaproc:ProcessSpec/0.1",
            "source_path": "example_plugin/main.process.md",
            "prompt_paths": ["predict.runbook.md"],
            "prompt_prefix": "You are a predictor.",
            "inputs": {"tickers": IOSpec(path="deps.tickers", type="list")},
            "outputs": {"predictions": IOSpec(path="out.md", type="path")},
            "with": {},
            "adapter": AdapterSummary(type="claude-code-cli", model="opus", tools=["bash"]),
            "env": {},
            "max_budget_usd": 1.0,
            "reuse_policy": "validated_outputs",
            "fan_out": FanOutDetails(
                over="tickers",
                bind="ticker",
                bind_fields=["ticker"],
                batch_size=10,
                retry=RetryPolicy(max_retries=2),
            ),
        }
    )


def _sample_dep_details() -> DepDetails:
    return DepDetails(
        dep_name="tickers",
        path="data/tickers.yaml",
        usage=["for_each.over", "inputs"],
        as_type="list",
        parse={"format": "yaml"},
        produced_by="scaffold-day",
        consumers=["predict", "retro"],
        process_schema_token="metaproc:ProcessSpec/0.1",
        source_path="example_plugin/main.process.md",
    )


def _sample_process_header() -> ProcessHeader:
    return ProcessHeader(
        name="example_plugin",
        description="Predict → retro → learn → mine",
        process_schema_token="metaproc:ProcessSpec/0.1",
        source_path="example_plugin/main.process.md",
        process_inputs={
            "run_id": InputSpec(name="run_id", param="run-id", as_type="string"),
        },
        defaults=DefaultsBlock(
            default_adapter="claude-code-cli",
            adapters={"claude-code-cli": AdapterSummary(type="claude-code-cli")},
        ),
        registered_schemas=["metaproc:Prediction/0.1"],
        body_markdown="## About\n\nLifecycle orchestrator.",
    )


@pytest.mark.parametrize(
    ("factory", "schema_token"),
    [
        (
            lambda: VizNode(id="n1", kind="step", label="predict", step=_sample_step_details()),
            "metaproc:VizNode/0.1",
        ),
        (
            lambda: VizEdge(source="a", target="b", kind="needs"),
            "metaproc:VizEdge/0.2",
        ),
        (lambda: _sample_step_details(), "metaproc:StepDetails/0.1"),
        (lambda: _sample_dep_details(), "metaproc:DepDetails/0.1"),
        (lambda: _sample_process_header(), "metaproc:ProcessHeader/0.1"),
        (
            lambda: NodeProgress(state="running", completed=3, total=10),
            "metaproc:NodeProgress/0.1",
        ),
        (
            lambda: ProgressSnapshot(
                run_dir="runs/2026-04-21",
                nodes={"predict": NodeProgress(state="completed", completed=10, total=10)},
            ),
            "metaproc:ProgressSnapshot/0.1",
        ),
        (
            lambda: NodeDecoration(badge="v3", icon="spark", accent_token="--accent-earnings"),
            "metaproc:NodeDecoration/0.1",
        ),
        (
            lambda: VizViewState(
                process_path="example_plugin/main.process.md",
                expanded_composites=["predict"],
                zoom=1.5,
            ),
            "metaproc:VizViewState/0.1",
        ),
    ],
)
def test_model_round_trips_and_carries_schema_token(factory, schema_token):
    original = factory()
    assert original.model_dump(by_alias=True)["schema"] == schema_token
    restored = type(original).model_validate_json(original.model_dump_json(by_alias=True))
    assert restored == original


def test_viz_model_round_trip_with_full_payload():
    header = _sample_process_header()
    node = VizNode(id="predict", kind="step", label="predict", step=_sample_step_details())
    dep_node = VizNode(id="dep:tickers", kind="dep", label="tickers", dep=_sample_dep_details())
    edge = VizEdge(source="predict", target="dep:tickers", kind="produces", label="tickers")
    progress = ProgressSnapshot(
        run_dir="runs/2026-04-21",
        nodes={"predict": NodeProgress(state="completed", completed=10, total=10)},
    )
    model = VizModel(
        root_process="example_plugin/main.process.md",
        header=header,
        nodes=[node, dep_node],
        edges=[edge],
        levels=[["predict"]],
        subgraphs={"root": ["predict"]},
        progress=progress,
    )

    restored = VizModel.model_validate_json(model.model_dump_json(by_alias=True))
    assert restored == model
    assert restored.model_dump(by_alias=True)["schema"] == "metaproc:VizModel/0.2"
    assert restored.header.name == "example_plugin"
    assert restored.nodes[0].step is not None
    assert restored.nodes[0].step.fan_out is not None
    assert restored.nodes[0].step.fan_out.retry is not None


def test_laid_out_round_trip():
    header = _sample_process_header()
    model = VizModel(root_process="p.md", header=header)
    laid_out = LaidOut(
        model=model,
        nodes=[LaidOutNode(id="n1", x=0.0, y=0.0, width=100.0, height=40.0)],
        edges=[LaidOutEdge(source="n1", target="n2", waypoints=[(0.0, 0.0), (10.0, 10.0)])],
        width=200.0,
        height=100.0,
    )
    restored = LaidOut.model_validate_json(laid_out.model_dump_json(by_alias=True))
    assert restored == laid_out
    assert restored.model_dump(by_alias=True)["schema"] == "metaproc:LaidOut/0.1"


def test_step_details_preserves_with_alias():
    step = StepDetails.model_validate(
        {
            "step_id": "composite",
            "mode": "composite",
            "uses_path": "child/process.md",
            "with": {"date": "2026-04-21"},
            "process_schema_token": "metaproc:ProcessSpec/0.1",
            "source_path": "x/process.md",
        }
    )
    dumped = step.model_dump(by_alias=True)
    assert dumped["with"] == {"date": "2026-04-21"}
    restored = StepDetails.model_validate_json(step.model_dump_json(by_alias=True))
    assert restored.with_ == {"date": "2026-04-21"}
