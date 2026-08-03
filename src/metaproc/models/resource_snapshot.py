"""Immutable launch-time resource topology and budget snapshot contracts."""

from __future__ import annotations

from typing import ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from metaproc.models.resource_budget import ResourceBudgetSpec
from metaproc.models.resources import Node, NodeType

RESOURCE_SNAPSHOT_SCHEMA = "metaproc.resource-snapshot/v1"


class ResourceTopologyNode(BaseModel):
    """Lean structural node persisted without mutable projection fields."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    node_type: NodeType
    node_id: str
    label: str
    parent_id: str | None = None
    children: list[ResourceTopologyNode] = Field(default_factory=list)

    @classmethod
    def from_node(cls, node: Node) -> Self:
        return cls(
            node_type=node.node_type,
            node_id=node.node_id,
            label=node.label,
            parent_id=node.parent_id,
            children=[cls.from_node(child) for child in node.children],
        )

    def to_node(self) -> Node:
        return Node(
            node_type=self.node_type,
            node_id=self.node_id,
            label=self.label,
            parent_id=self.parent_id,
            children=[child.to_node() for child in self.children],
        )


class ResourceRunSnapshot(BaseModel):
    """Versioned resource contract frozen into ``run-config.yaml`` once."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    schema_version: Literal["metaproc.resource-snapshot/v1"] = Field(
        default=RESOURCE_SNAPSHOT_SCHEMA,
        alias="schema",
    )
    hierarchy: ResourceTopologyNode
    budgets: list[ResourceBudgetSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_snapshot(self) -> Self:
        seen: set[str] = set()

        def walk(node: ResourceTopologyNode, expected_parent: str | None) -> None:
            if node.node_id in seen:
                raise ValueError(f"duplicate resource topology node ID {node.node_id!r}")
            if node.parent_id != expected_parent:
                raise ValueError(
                    f"resource topology node {node.node_id!r} has parent {node.parent_id!r}; "
                    f"expected {expected_parent!r}"
                )
            seen.add(node.node_id)
            for child in node.children:
                walk(child, node.node_id)

        walk(self.hierarchy, None)
        budget_ids = [budget.budget_id for budget in self.budgets]
        if len(budget_ids) != len(set(budget_ids)):
            raise ValueError("resource snapshot budget IDs must be unique")
        return self

    def hierarchy_node(self) -> Node:
        return self.hierarchy.to_node()
