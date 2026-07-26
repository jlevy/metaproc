"""Tests for the resource hierarchy skeleton builder (B5, spec §1 + §4.4.2)."""

from __future__ import annotations

from pathlib import Path

from metaproc.engine.resource_hierarchy import build_hierarchy_skeleton
from metaproc.io.state_io import write_status_at
from metaproc.models.runtime import StatusRecord
from metaproc.viz_loader import load_plan_bundle


def _write(tmp_path: Path, rel: str, content: str) -> Path:
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


# Reuse the parent/child composite fixtures the viz tests already pin down so
# the resource hierarchy is validated against the same canonical shape the
# Visual tab uses (otherwise the two surfaces would drift).

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


def test_skeleton_root_node_is_run_with_process_child(tmp_path: Path) -> None:
    parent_path = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    _write(tmp_path, "parent/child/test.process.md", _CHILD_PROCESS)
    bundle = load_plan_bundle(parent_path, params={"CUTOFF_DATE": "2026-04-21"})

    root = build_hierarchy_skeleton(bundle, run_id="run-1")

    assert root.node_type == "run"
    assert root.node_id == "run-1"
    assert root.parent_id is None
    assert len(root.children) == 1
    process_node = root.children[0]
    assert process_node.node_type == "process"
    assert process_node.node_id == "process:root"
    assert process_node.parent_id == "run-1"


def test_skeleton_step_node_ids_match_viz_qualified_ids(tmp_path: Path) -> None:
    """Spec §1: must reuse the viz plane's qualified IDs verbatim."""
    parent_path = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    _write(tmp_path, "parent/child/test.process.md", _CHILD_PROCESS)
    bundle = load_plan_bundle(parent_path, params={"CUTOFF_DATE": "2026-04-21"})

    root = build_hierarchy_skeleton(bundle, run_id="run-1")
    process_root = root.children[0]
    composite_step = process_root.children[0]

    # Root-level step keeps its bare step_id.
    assert composite_step.node_id == "run_child"
    assert composite_step.parent_id == "process:root"

    # Composite step recurses into a nested process node...
    assert len(composite_step.children) == 1
    nested_process = composite_step.children[0]
    assert nested_process.node_type == "process"
    assert nested_process.node_id == "process:run_child"
    assert nested_process.parent_id == "run_child"

    # ...whose steps use the parent::child qualified form.
    leaf = nested_process.children[0]
    assert leaf.node_type == "step"
    assert leaf.node_id == "run_child::leaf"
    assert leaf.parent_id == "process:run_child"


def test_skeleton_omits_item_layer_without_run_dir(tmp_path: Path) -> None:
    """When ``run_dir`` is None, callers get just the structural skeleton."""
    parent_path = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    _write(tmp_path, "parent/child/test.process.md", _CHILD_PROCESS)
    bundle = load_plan_bundle(parent_path, params={"CUTOFF_DATE": "2026-04-21"})

    root = build_hierarchy_skeleton(bundle, run_id="run-1")
    leaf = root.children[0].children[0].children[0].children[0]
    assert leaf.children == []


def test_skeleton_attaches_item_layer_when_run_dir_present(tmp_path: Path) -> None:
    parent_path = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    _write(tmp_path, "parent/child/test.process.md", _CHILD_PROCESS)
    bundle = load_plan_bundle(parent_path, params={"CUTOFF_DATE": "2026-04-21"})

    run_dir = tmp_path / "runs" / "2026-04-21"
    # Composite child has its own run_dir at <run>/run_child/; per-task state
    # for its `leaf` step lives at <child_run>/.state/tasks/leaf/.
    leaf_state = run_dir / "run_child" / ".state" / "tasks" / "leaf"
    leaf_state.mkdir(parents=True)
    write_status_at(
        leaf_state,
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

    root = build_hierarchy_skeleton(bundle, run_id="run-1", run_dir=run_dir)
    leaf_step = root.children[0].children[0].children[0].children[0]
    assert leaf_step.node_id == "run_child::leaf"
    # The leaf step has at least the one item we wrote a status for.
    assert len(leaf_step.children) >= 1
    item_node = leaf_step.children[0]
    assert item_node.node_type == "item"
    assert item_node.parent_id == leaf_step.node_id
    assert item_node.node_id.startswith("run_child::leaf::")


def test_skeleton_metrics_default_to_empty(tmp_path: Path) -> None:
    """The skeleton owns no metrics — that's the joiner's job (resobs-B6)."""
    parent_path = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    _write(tmp_path, "parent/child/test.process.md", _CHILD_PROCESS)
    bundle = load_plan_bundle(parent_path, params={"CUTOFF_DATE": "2026-04-21"})

    root = build_hierarchy_skeleton(bundle, run_id="run-1")
    assert root.self_metrics.wall_time_s is None
    assert root.total_metrics.wall_time_s is None
    assert root.attribution == "measured"
