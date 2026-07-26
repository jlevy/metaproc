"""Visualization schemas for the process-visualization plane.

Every class here is a first-class Pydantic model with a ``schema`` token following
the metaproc envelope convention. Types in this module are pure data contracts
consumed by three renderers:

- The browser plugin (``metaproc/metabrowser_plugin/plugin/viz.js``)
- The static SVG renderer (``metaproc/src/metaproc/viz/render_svg.py``)
- The ASCII topology formatter used by ``metaproc status --topology``

The projection from a resolved :class:`metaproc.models.plan.Plan` to a
:class:`VizModel` lives in ``metaproc/src/metaproc/viz/project.py``. This file
defines shapes only — no projection or rendering logic.

See ``metaproc/docs/arch/arch-metaproc-core.md`` for the surrounding architecture.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from metaproc.models.authored import IOSpec, RetryPolicy

# ── Leaf / sub-types ────────────────────────────────────────────
# Embedded in StepDetails / DepDetails / ProcessHeader. These do not carry their
# own schema tokens — they are not serialized independently.


class AdapterSummary(BaseModel):
    """Flattened adapter config for display, distilled from ResolvedAdapter.config.

    The raw ResolvedAdapter.config is an adapter-type-specific dict. This model
    picks the keys that are visually meaningful across all adapter types.
    """

    type: str
    model: str | None = None
    provider: str | None = None
    tools: list[str] = Field(default_factory=list)
    timeout_s: int | None = None
    max_budget_usd: float | None = None


class FanOutDetails(BaseModel):
    """Fan-out summary shown on a step node."""

    over: str
    bind: str | None = None
    bind_fields: list[str] = Field(default_factory=list)
    batch_size: int | None = None
    retry: RetryPolicy | None = None
    item_count: int | None = None


class InputSpec(BaseModel):
    """One entry of ``process.inputs`` (operator binding)."""

    name: str
    param: str | None = None
    as_type: str
    default: str | int | float | bool | None = None
    description: str | None = None


class DefaultsBlock(BaseModel):
    """Process-level ``defaults`` block."""

    default_adapter: str | None = None
    adapters: dict[str, AdapterSummary] = Field(default_factory=dict)
    retry: RetryPolicy | None = None


class IOSummaryEntry(BaseModel):
    """One compact IO entry shown directly on a step card.

    Distinct from :class:`metaproc.models.authored.IOSpec`: this is a
    render-side projection with just the fields the card shows. ``produced_by``
    is filled in for inputs (when the referenced dep has a producer) and
    always ``None`` for outputs.
    """

    name: str
    as_type: str | None = None
    produced_by: str | None = None


# ── Node-detail payloads ────────────────────────────────────────


class StepDetails(BaseModel):
    """Exhaustive step payload — one slot per authored step field."""

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    schema_: str = Field(default="metaproc:StepDetails/0.1", alias="schema")

    step_id: str
    mode: Literal["code", "agent", "composite", "manual"]
    description: str | None = None
    needs: list[str] = Field(default_factory=list)

    # Defining process metadata (drawer's "Identity" section mirrors the
    # process drawer so every entity exposes: what it is, where it's
    # defined, and the schema token of the spec that defines it).
    process_schema_token: str
    source_path: str

    handler: str | None = None
    command: str | None = None
    uses_path: str | None = None
    prompt_paths: list[str] = Field(default_factory=list)
    prompt_prefix: str | None = None

    inputs: dict[str, IOSpec] = Field(default_factory=dict)
    outputs: dict[str, IOSpec] = Field(default_factory=dict)
    output_root: str | None = None
    with_: dict[str, str] = Field(default_factory=dict, alias="with")

    adapter: AdapterSummary | None = None
    variant: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    max_budget_usd: float | None = None
    token_budget: int | None = None
    reuse_policy: Literal["validated_outputs", "exact_inputs", "never"] | None = None

    fan_out: FanOutDetails | None = None

    # Compact IO summaries for on-canvas step cards. Longer form stays in
    # ``inputs`` / ``outputs`` above and the side-panel detail view.
    inputs_summary: list[IOSummaryEntry] = Field(default_factory=list)
    outputs_summary: list[IOSummaryEntry] = Field(default_factory=list)


class DepDetails(BaseModel):
    """Exhaustive dep payload — one slot per authored dep field."""

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    schema_: str = Field(default="metaproc:DepDetails/0.1", alias="schema")

    dep_name: str
    path: str
    usage: list[str] = Field(default_factory=list)
    as_type: str | None = None
    parse: dict[str, object] | None = None
    produced_by: str | None = None
    consumers: list[str] = Field(default_factory=list)

    # Defining process metadata — see StepDetails for rationale.
    process_schema_token: str
    source_path: str


class ProcessHeader(BaseModel):
    """Process identity + operator bindings + defaults + markdown body."""

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    schema_: str = Field(default="metaproc:ProcessHeader/0.1", alias="schema")

    name: str
    description: str | None = None
    process_schema_token: str
    source_path: str
    process_inputs: dict[str, InputSpec] = Field(default_factory=dict)
    defaults: DefaultsBlock = Field(default_factory=DefaultsBlock)
    registered_schemas: list[str] = Field(default_factory=list)
    body_markdown: str = ""


# ── Progress overlay ────────────────────────────────────────────


class NodeProgress(BaseModel):
    """One step's runtime state; embedded in ProgressSnapshot."""

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    schema_: str = Field(default="metaproc:NodeProgress/0.1", alias="schema")

    state: Literal["pending", "running", "completed", "failed", "blocked", "skipped"]
    completed: int = 0
    total: int = 0
    elapsed_s: float | None = None
    last_error: str | None = None
    failure_class: str | None = None
    runtime_artifacts: list[str] = Field(default_factory=list)


class ProgressSnapshot(BaseModel):
    """Per-step runtime overlay; composes with VizModel at render time."""

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    schema_: str = Field(default="metaproc:ProgressSnapshot/0.1", alias="schema")

    run_dir: str
    generated_at: str = ""
    nodes: dict[str, NodeProgress] = Field(default_factory=dict)


# ── Graph structure ─────────────────────────────────────────────


class VizNode(BaseModel):
    """One visual node (step / dep / process / file)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    schema_: str = Field(default="metaproc:VizNode/0.1", alias="schema")

    id: str
    kind: Literal["step", "dep", "process", "file"]
    label: str
    path: str | None = None
    parent: str | None = None
    step: StepDetails | None = None
    dep: DepDetails | None = None
    process: ProcessHeader | None = None
    progress: NodeProgress | None = None


class VizEdge(BaseModel):
    """One visual edge (needs / produces / consumes).

    Containment is *not* an edge kind — it is expressed by :attr:`VizNode.parent`
    and :attr:`VizModel.subgraphs` and rendered as an enclosing frame, never as
    an arrow (see §Visual design principles in the process-visualization plan).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    schema_: str = Field(default="metaproc:VizEdge/0.2", alias="schema")

    source: str
    target: str
    kind: Literal["needs", "produces", "consumes"]
    label: str | None = None


class VizModel(BaseModel):
    """Structural projection of a Plan (+ optional runtime).

    This is the single data contract every renderer reads. Plane selection
    (Process Tree / Step DAG / Artifact Graph / File Layout) is a view-state
    decision that lives in :class:`VizViewState`, not on this model.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    schema_: str = Field(default="metaproc:VizModel/0.2", alias="schema")

    root_process: str
    header: ProcessHeader
    nodes: list[VizNode] = Field(default_factory=list)
    edges: list[VizEdge] = Field(default_factory=list)
    levels: list[list[str]] = Field(default_factory=list)
    subgraphs: dict[str, list[str]] = Field(default_factory=dict)
    root_process_node: str | None = None
    progress: ProgressSnapshot | None = None


# ── Layout + rendering ──────────────────────────────────────────


class LaidOutNode(BaseModel):
    """A VizNode with computed layout coordinates."""

    id: str
    x: float
    y: float
    width: float
    height: float


class LaidOutEdge(BaseModel):
    """A VizEdge with computed waypoints."""

    source: str
    target: str
    waypoints: list[tuple[float, float]] = Field(default_factory=list)


class LaidOut(BaseModel):
    """VizModel + computed xy + edge waypoints. Produced by the layout engine."""

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    schema_: str = Field(default="metaproc:LaidOut/0.1", alias="schema")

    model: VizModel
    nodes: list[LaidOutNode] = Field(default_factory=list)
    edges: list[LaidOutEdge] = Field(default_factory=list)
    width: float = 0.0
    height: float = 0.0


# ── Plugin decorators ───────────────────────────────────────────


class NodeDecoration(BaseModel):
    """Plugin-contributed visual annotation merged into a rendered node.

    Returned by callbacks registered via
    :func:`metaproc.plugins.registry.register_viz_decorator`. The renderer
    merges a decoration into the rendered node by adding a badge, icon,
    accent color, or tooltip addendum — it never mutates the source VizModel.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    schema_: str = Field(default="metaproc:NodeDecoration/0.1", alias="schema")

    badge: str | None = None
    icon: str | None = None
    accent_token: str | None = None
    tooltip_addendum: str | None = None


# ── Client view state ───────────────────────────────────────────


class VizViewState(BaseModel):
    """Client-side view state — persisted to localStorage per process.

    This is separate from :class:`VizModel` on purpose: toggling a plane,
    expanding a composite, zooming in, hovering a node — none of these are
    data mutations. They are rendering selections.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    schema_: str = Field(default="metaproc:VizViewState/0.1", alias="schema")

    process_path: str
    active_plane: Literal["tree", "dag", "artifact", "file"] = "tree"
    expanded_composites: list[str] = Field(default_factory=list)
    zoom: float = 1.0
    focused_node: str | None = None
    sidebar_open: bool = True
