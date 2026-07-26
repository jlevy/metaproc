"""Contract tests for ``metaproc.viz.project.build_viz_model``.

Covers four guarantees (see the Phase 1 bead):

* Field coverage — every authored step/dep field round-trips into StepDetails /
  DepDetails. If a field is added to the authored surface without updating the
  projection, this test tells us.
* Reachability — every dep, every step, and every composite child surfaces as
  a ``VizNode``. No silently-dropped nodes.
* Drill-down — every path-bearing field on the emitted VizModel points to a
  real path on disk (or a templated path that's explicitly allowed).
* Round-trip — the emitted VizModel and its nested schemas survive a JSON
  round-trip via ``model_dump_json`` / ``model_validate_json``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from metaproc.io.state_io import write_status_at
from metaproc.models.authored import ProcessSpec
from metaproc.models.plan import Plan
from metaproc.models.runtime import StatusRecord
from metaproc.models.viz import (
    NodeProgress,
    ProgressSnapshot,
    VizModel,
)
from metaproc.viz import build_viz_model
from metaproc.viz.project import PlanBundle
from metaproc.viz_loader import load_plan_bundle, scan_bundle_progress


def _write(tmp_path: Path, rel: str, content: str) -> Path:
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


# ── Synthetic process — exercises every step / dep field ─────────


_SYNTHETIC_PROCESS = """\
---
process:
  schema: metaproc:ProcessSpec/0.1
  name: synthetic
  description: Exercises every authored field for projection coverage.
  defaults:
    default_adapter: main
    adapters:
      main:
        type: pi-cli
        config:
          model: sonnet
          provider: anthropic
          tools:
            - bash
            - read
          timeout_s: 300
          max_budget_usd: 2.5
    retry:
      max_retries: 3
      initial_backoff_s: 2.0
      backoff_multiplier: 2.0
      max_backoff_s: 30.0
  inputs:
    cutoff_date:
      param: CUTOFF_DATE
      as: string
      description: cutoff date
      required: true
    items_path:
      as: path
      description: items file
      required: false
  deps:
    tickers:
      path: data/tickers.yaml
      as: list<string>
      parse:
        format: yaml
        extract: progress.items
    predict_runbook:
      path: prompts/predict.runbook.md
      as: path
    predictions_out:
      path: "{{run.dir}}/predict/{{ticker}}/prediction.md"
      as: path
      produced_by: predict
  steps:
    - id: scaffold
      mode: code
      description: Build the day's scaffold.
      handler: metaproc.code_steps.scaffold
      needs: []
      env:
        SCAFFOLD_FLAG: "1"
      output_root: "runs/scaffold"
      max_budget_usd: 0.25
      token_budget: 10000
      reuse_policy: validated_outputs
    - id: predict
      mode: agent
      description: Predict for each ticker.
      needs:
        - scaffold
      prompt_paths:
        - deps.predict_runbook
      prompt_prefix: "You are a predictor."
      for_each:
        over: deps.tickers
        bind: ticker
        bind_fields:
          - ticker
        batch_size: 5
        retry:
          max_retries: 2
          initial_backoff_s: 1.5
          backoff_multiplier: 2.0
          max_backoff_s: 20.0
      inputs:
        tickers: deps.tickers
      outputs:
        prediction:
          path: "{{run.dir}}/predict/{{ticker}}/prediction.md"
      variant: sonnet
      max_budget_usd: 5.0
      reuse_policy: exact_inputs
    - id: retro
      mode: manual
      description: Human retrospective.
      needs:
        - predict
---

# Synthetic process body.
"""


def _build_synthetic_bundle(tmp_path: Path) -> Path:
    process_path = _write(tmp_path, "synthetic/test.process.md", _SYNTHETIC_PROCESS)
    # Create the dep files so build_plan's validation is satisfied.
    _write(
        tmp_path,
        "synthetic/data/tickers.yaml",
        "---\nprogress:\n  items:\n    - ticker: AAPL\n    - ticker: MSFT\n    - ticker: GOOG\n---\n",
    )
    _write(
        tmp_path,
        "synthetic/prompts/predict.runbook.md",
        "---\nrunbook: predict\n---\n\nPredict.\n",
    )
    return process_path


def test_field_coverage_every_step_and_dep_field_round_trips(tmp_path: Path) -> None:
    process_path = _build_synthetic_bundle(tmp_path)
    bundle = load_plan_bundle(process_path, params={"CUTOFF_DATE": "2026-04-21"})
    viz = build_viz_model(bundle)

    steps_by_id = {n.id: n for n in viz.nodes if n.kind == "step" and n.step is not None}
    deps_by_name = {n.dep.dep_name: n for n in viz.nodes if n.kind == "dep" and n.dep is not None}

    # Step: every authored field is populated
    predict = steps_by_id["predict"].step
    assert predict is not None
    assert predict.step_id == "predict"
    assert predict.mode == "agent"
    assert predict.description == "Predict for each ticker."
    assert "scaffold" in predict.needs
    assert predict.prompt_paths  # resolved from deps.predict_runbook
    assert predict.prompt_prefix == "You are a predictor."
    assert predict.adapter is not None
    assert predict.adapter.type == "pi-cli"
    assert predict.adapter.model == "sonnet"
    assert predict.adapter.provider == "anthropic"
    assert "bash" in predict.adapter.tools
    assert predict.adapter.timeout_s == 300
    assert predict.adapter.max_budget_usd == 2.5
    assert predict.variant == "sonnet"
    assert predict.max_budget_usd == 5.0
    assert predict.reuse_policy == "exact_inputs"
    assert predict.fan_out is not None
    assert predict.fan_out.over == "deps.tickers"
    assert predict.fan_out.bind == "ticker"
    assert predict.fan_out.batch_size == 5
    assert predict.fan_out.retry is not None
    assert predict.fan_out.retry.max_retries == 2
    assert predict.fan_out.item_count == 3  # three tickers

    scaffold = steps_by_id["scaffold"].step
    assert scaffold is not None
    assert scaffold.handler == "metaproc.code_steps.scaffold"
    assert scaffold.env == {"SCAFFOLD_FLAG": "1"}
    assert scaffold.max_budget_usd == 0.25
    assert scaffold.token_budget == 10000
    assert scaffold.reuse_policy == "validated_outputs"
    assert scaffold.output_root == "runs/scaffold"

    # Dep: every authored field is populated
    tickers = deps_by_name["tickers"].dep
    assert tickers is not None
    assert tickers.path.endswith("data/tickers.yaml")
    assert "for_each.over" in tickers.usage
    assert "inputs" in tickers.usage
    assert tickers.as_type == "list<string>"
    assert tickers.parse is not None
    assert tickers.parse.get("format") == "yaml"
    assert "predict" in tickers.consumers

    predictions_out = deps_by_name["predictions_out"].dep
    assert predictions_out is not None
    assert predictions_out.produced_by == "predict"


def test_step_io_summaries_pull_as_type_and_producer_from_deps(tmp_path: Path) -> None:
    process_path = _build_synthetic_bundle(tmp_path)
    bundle = load_plan_bundle(process_path, params={"CUTOFF_DATE": "2026-04-21"})
    viz = build_viz_model(bundle)

    steps_by_id = {n.id: n for n in viz.nodes if n.kind == "step" and n.step is not None}
    predict = steps_by_id["predict"].step
    assert predict is not None

    # `inputs: { tickers: deps.tickers }` → IO summary enters with the dep's as_type
    # and producer (None here; items file is authored, not produced by a step).
    tickers_in = next(e for e in predict.inputs_summary if e.name == "tickers")
    assert tickers_in.as_type == "list<string>"
    assert tickers_in.produced_by is None

    # Output `prediction: { path: ... }` has no dep ref → no as_type, no producer.
    assert [e.name for e in predict.outputs_summary] == ["prediction"]
    prediction_out = predict.outputs_summary[0]
    assert prediction_out.as_type is None
    assert prediction_out.produced_by is None


def test_reachability_every_dep_and_step_emits_a_node(tmp_path: Path) -> None:
    process_path = _build_synthetic_bundle(tmp_path)
    bundle = load_plan_bundle(process_path, params={"CUTOFF_DATE": "2026-04-21"})
    viz = build_viz_model(bundle)

    step_ids = {s.step_id for s in bundle.plan.steps}
    dep_names = set(bundle.plan.deps.keys())

    emitted_step_labels = {n.label for n in viz.nodes if n.kind == "step"}
    emitted_dep_labels = {n.label for n in viz.nodes if n.kind == "dep"}

    assert step_ids <= emitted_step_labels
    assert dep_names <= emitted_dep_labels


def test_header_populated_from_authored_spec(tmp_path: Path) -> None:
    process_path = _build_synthetic_bundle(tmp_path)
    bundle = load_plan_bundle(process_path, params={"CUTOFF_DATE": "2026-04-21"})
    viz = build_viz_model(bundle)

    header = viz.header
    assert header.name == "synthetic"
    assert header.description is not None
    assert header.process_schema_token.startswith("metaproc:ProcessSpec/")
    assert header.body_markdown.strip().startswith("# Synthetic process body")
    assert "cutoff_date" in header.process_inputs
    assert header.process_inputs["cutoff_date"].param == "CUTOFF_DATE"
    assert header.defaults.default_adapter == "main"
    assert "main" in header.defaults.adapters
    assert header.defaults.adapters["main"].model == "sonnet"


def test_edges_include_needs_produces_and_consumes(tmp_path: Path) -> None:
    process_path = _build_synthetic_bundle(tmp_path)
    bundle = load_plan_bundle(process_path, params={"CUTOFF_DATE": "2026-04-21"})
    viz = build_viz_model(bundle)

    kinds = {(e.source, e.target, e.kind) for e in viz.edges}
    assert ("scaffold", "predict", "needs") in kinds
    # predict produces predictions_out
    assert ("predict", "dep:predictions_out", "produces") in kinds
    # predict consumes tickers via inputs.tickers: deps.tickers
    assert any(
        e.source == "predict" and e.kind == "consumes" and e.target.endswith("dep:tickers")
        for e in viz.edges
    )


def test_drill_down_paths_resolve_on_disk(tmp_path: Path) -> None:
    process_path = _build_synthetic_bundle(tmp_path)
    bundle = load_plan_bundle(process_path, params={"CUTOFF_DATE": "2026-04-21"})
    viz = build_viz_model(bundle)

    process_dir = Path(bundle.source_path).parent

    for node in viz.nodes:
        if node.kind != "dep" or node.dep is None:
            continue
        # Dep path is authored relative to the process directory.
        dep_path = (process_dir / node.dep.path).resolve()
        assert dep_path.exists() or "{{" in node.dep.path, (
            f"dep {node.dep.dep_name!r} points at non-existent path {dep_path}"
        )


def test_vizmodel_round_trips_through_json(tmp_path: Path) -> None:
    process_path = _build_synthetic_bundle(tmp_path)
    bundle = load_plan_bundle(process_path, params={"CUTOFF_DATE": "2026-04-21"})
    viz = build_viz_model(bundle)

    restored = VizModel.model_validate_json(viz.model_dump_json(by_alias=True))
    assert restored == viz


def test_progress_overlay_attached_per_step(tmp_path: Path) -> None:
    process_path = _build_synthetic_bundle(tmp_path)
    bundle = load_plan_bundle(process_path, params={"CUTOFF_DATE": "2026-04-21"})
    snapshot = ProgressSnapshot(
        run_dir="runs/2026-04-21",
        nodes={"predict": NodeProgress(state="running", completed=1, total=3)},
    )
    viz = build_viz_model(bundle, progress=snapshot)
    progress_by_label = {n.label: n.progress for n in viz.nodes if n.kind == "step"}
    assert progress_by_label["predict"] is not None
    assert progress_by_label["predict"].state == "running"
    assert progress_by_label["scaffold"] is None


# ── Composite recursion ──────────────────────────────────────────


_PARENT_PROCESS = """\
---
process:
  name: parent
  deps:
    child_proc:
      path: child/test.process.md
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
  inputs:
    cutoff_date:
      param: CUTOFF_DATE
      as: string
  steps:
    - id: leaf
      mode: code
      handler: metaproc.code_steps.leaf
---

Child body.
"""


def test_composite_children_nest_inside_a_child_process_node(tmp_path: Path) -> None:
    parent_path = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    _write(tmp_path, "parent/child/test.process.md", _CHILD_PROCESS)

    bundle = load_plan_bundle(parent_path, params={"CUTOFF_DATE": "2026-04-21"})
    assert "run_child" in bundle.children  # child loaded recursively

    viz = build_viz_model(bundle)

    # Root process node exists and has no parent.
    root_process = next(n for n in viz.nodes if n.id == "process:root")
    assert root_process.kind == "process"
    assert root_process.parent is None
    assert viz.root_process_node == "process:root"

    # Parent composite step is present, parented at the root process node.
    composite = next(n for n in viz.nodes if n.kind == "step" and n.label == "run_child")
    assert composite.parent == "process:root"

    # Child process node is parented at the composite step.
    child_process = next(n for n in viz.nodes if n.id == "process:run_child")
    assert child_process.kind == "process"
    assert child_process.parent == "run_child"

    # Child leaf step is inside the child process node.
    leaf = next(n for n in viz.nodes if n.kind == "step" and n.label == "leaf")
    assert leaf.parent == "process:run_child"

    # No `contains` edges — containment is implicit via parent/subgraph nesting.
    assert all(e.kind != "contains" for e in viz.edges)


def test_scan_bundle_progress_recurses_into_composite_children(tmp_path: Path) -> None:
    """Progress overlay must pick up child-step status under a composite's nested run dir."""

    parent_path = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    _write(tmp_path, "parent/child/test.process.md", _CHILD_PROCESS)
    bundle = load_plan_bundle(parent_path, params={"CUTOFF_DATE": "2026-04-21"})

    run_dir = tmp_path / "runs" / "2026-04-21"
    # Child leaf step's status lives at the new per-task state path
    # inside the composite child's nested run dir.
    child_run_dir = run_dir / "run_child"
    leaf_state_dir = child_run_dir / ".state" / "tasks" / "leaf"
    leaf_state_dir.mkdir(parents=True)
    write_status_at(
        leaf_state_dir,
        StatusRecord(
            run_id="run-1",
            step_id="leaf",
            item={"step": "leaf"},
            state="completed",
            attempt=1,
            started_at="2026-04-21T01:00:00",
            completed_at="2026-04-21T01:05:00",
        ),
    )

    snapshot = scan_bundle_progress(run_dir, bundle)

    # Child step appears keyed by its qualified node id (parent::child form).
    assert "run_child::leaf" in snapshot.nodes
    assert snapshot.nodes["run_child::leaf"].state == "completed"

    # build_viz_model attaches the progress to the correct child node.
    viz = build_viz_model(bundle, progress=snapshot)
    leaf_node = next(n for n in viz.nodes if n.kind == "step" and n.label == "leaf")
    assert leaf_node.progress is not None
    assert leaf_node.progress.state == "completed"


# ── Loader input validation ──────────────────────────────────────


def test_load_plan_bundle_fails_clearly_on_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "nope" / "test.process.md"
    with pytest.raises(Exception):  # noqa: PT011, B017, BLE001
        load_plan_bundle(missing)


def test_empty_plan_yields_empty_nodes_and_edges(tmp_path: Path) -> None:
    """Sanity: the projection handles a plan with no steps or deps."""

    plan = Plan(process="empty/test.process.md")
    spec = ProcessSpec(name="empty")

    bundle = PlanBundle(plan=plan, spec=spec, source_path="empty/test.process.md")
    viz = build_viz_model(bundle)
    # An "empty" plan still has a root process node (always exactly one).
    assert len(viz.nodes) == 1
    assert viz.nodes[0].kind == "process"
    assert viz.nodes[0].id == "process:root"
    assert viz.edges == []
    assert viz.header.name == "empty"
