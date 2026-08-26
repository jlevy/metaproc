"""Canonical qualified-id helpers for process / step / dep nodes.

Lives in ``metaproc.models`` (alongside the other neutral data models)
so engine, runtime, and viz layers can all consume the same id contract
without anyone reaching into the visualization package.

Runtime code, hierarchy loading, and the browser projection all need the
same qualified IDs. Keeping the helpers here avoids coupling engine or
runtime modules to private visualization helpers.

The viz package re-exports these names so existing helpers
(``_step_node_id`` etc.) remain valid call sites for backwards
compatibility while new code uses the public, neutral names below.
"""

from __future__ import annotations

ROOT_SUBGRAPH_KEY = "root"
"""Sentinel for the top-level subgraph (root process / root steps)."""


def step_node_id(subgraph_key: str, step_id: str) -> str:
    """Return the qualified id for a step under ``subgraph_key``.

    Root-level steps keep their bare ``step_id`` so single-process specs
    look the same as they always have. Composite descendants are
    namespaced ``parent::child`` so a composite step named ``leaf``
    inside ``extract-items`` resolves to ``extract-items::leaf`` — never
    colliding with a root-level ``leaf``.
    """
    return f"{subgraph_key}::{step_id}" if subgraph_key != ROOT_SUBGRAPH_KEY else step_id


def process_node_id(subgraph_key: str) -> str:
    """Return the stable id for the process node owning ``subgraph_key``.

    Prefixed with ``process:`` to avoid colliding with the composite
    step's own id when the subgraph key equals that step id (the
    standard root → first-level boundary case).
    """
    return f"process:{subgraph_key}"


def dep_node_id(subgraph_key: str, dep_name: str) -> str:
    """Return the qualified id for a dep node under ``subgraph_key``."""
    prefix = "" if subgraph_key == ROOT_SUBGRAPH_KEY else f"{subgraph_key}::"
    return f"{prefix}dep:{dep_name}"


def child_subgraph_key(parent_key: str, step_id: str) -> str:
    """Return the subgraph key of a composite step's nested process."""
    return step_id if parent_key == ROOT_SUBGRAPH_KEY else f"{parent_key}::{step_id}"
