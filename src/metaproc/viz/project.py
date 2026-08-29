"""Pure projection: ``Plan`` (+ authored ``ProcessSpec`` + optional progress) → ``VizModel``.

The projection is one-directional: ``engine → Plan → viz``. Callers provide a
:class:`PlanBundle` holding everything needed to emit the visual model — the
resolved plan, the authored spec, the source path, the spec body, and a
map of already-loaded child bundles for any composite steps. Loading children
from disk is the caller's job (composite recursion requires ``build_plan`` and
``load_process_spec`` which live in the engine layer).

The projector populates every authored step/dep field into ``StepDetails`` and
``DepDetails`` so drill-down panels have something real to render.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast

from metaproc.models.authored import (
    AdapterConfig,
    IOSpec,
    ProcessDefaults,
    ProcessInput,
    ProcessOutput,
    ProcessSpec,
    ValueType,
)
from metaproc.models.plan import FanOut, ResolvedAdapter, ResolvedDep, ResolvedStep
from metaproc.models.viz import (
    AdapterSummary,
    DefaultsBlock,
    DepDetails,
    FanOutDetails,
    InputSpec,
    IOSummaryEntry,
    OutputSpec,
    ProcessHeader,
    ProgressSnapshot,
    StepDetails,
    TaskOutputProjection,
    VizEdge,
    VizModel,
    VizNode,
)

ReusePolicy = Literal["validated_outputs", "exact_inputs", "never"]
_VALID_REUSE_POLICIES = frozenset({"validated_outputs", "exact_inputs", "never"})


# PlanBundle moved to metaproc.models.plan_bundle so engine and runtime
# code can consume it without reaching into the viz package. This re-export
# (with the explicit `as` form) keeps existing
# `from metaproc.viz.project import PlanBundle` callers working without
# triggering basedpyright's reportPrivateLocalImportUsage.
from metaproc.models.plan_bundle import PlanBundle as PlanBundle  # noqa: E402


def build_viz_model(
    bundle: PlanBundle,
    *,
    progress: ProgressSnapshot | None = None,
    task_projection: TaskOutputProjection | None = None,
) -> VizModel:
    """Project a :class:`PlanBundle` into a :class:`VizModel`.

    Each subgraph (the root, and every composite child process) materializes as
    a :class:`VizNode` with ``kind="process"`` carrying its own
    :class:`ProcessHeader`. Step and dep nodes in a subgraph point their
    ``parent`` field at that process node, giving the renderer a direct
    compound-graph tree to feed into a hierarchical layout engine. Composite
    step nodes sit as leaves in their parent subgraph; the child process node
    is nested *inside* the composite step, so an ELK-style walk of the
    ``parent`` chain yields: root-process → composite-step → child-process
    → child-steps.

    ``progress``, if given, attaches :class:`NodeProgress` entries to step nodes
    whose qualified node id appears in ``progress.nodes``. The key format
    matches :func:`_step_node_id` — bare ``step_id`` at the root level,
    ``parent::child`` for composite descendants — so progress for a child
    step never collides with a sibling of the same name at another level.

    ``task_projection`` is an optional read-only runtime view rebuilt by the caller
    from existing task records. The structural projector carries it without deriving
    or persisting another source of truth.
    """
    nodes: list[VizNode] = []
    edges: list[VizEdge] = []
    subgraphs: dict[str, list[str]] = {}

    header = _project_header(bundle.spec, bundle.source_path, bundle.body_markdown)
    root_process_node_id = _process_node_id("root")
    _project_bundle(
        bundle=bundle,
        parent_step_id=None,
        subgraph_key="root",
        nodes=nodes,
        edges=edges,
        subgraphs=subgraphs,
        progress=progress,
    )

    levels = _topological_levels(nodes, edges)

    return VizModel(
        root_process=bundle.source_path,
        header=header,
        nodes=nodes,
        edges=edges,
        levels=levels,
        subgraphs=subgraphs,
        root_process_node=root_process_node_id,
        progress=progress,
        task_projection=task_projection,
    )


# ── Recursive projection ────────────────────────────────────────


def _project_bundle(
    *,
    bundle: PlanBundle,
    parent_step_id: str | None,
    subgraph_key: str,
    nodes: list[VizNode],
    edges: list[VizEdge],
    subgraphs: dict[str, list[str]],
    progress: ProgressSnapshot | None,
) -> None:
    """Emit a process node + its steps + deps; recurse into composite children.

    ``parent_step_id`` is the composite step node that invoked this subgraph,
    or ``None`` for the root bundle. The process node for this subgraph takes
    that as its ``parent`` so the compound-graph tree nests the child process
    inside the composite step's box.
    """
    bucket: list[str] = []
    subgraphs[subgraph_key] = bucket

    process_node_id = _process_node_id(subgraph_key)
    nodes.append(
        VizNode(
            id=process_node_id,
            kind="process",
            label=bundle.spec.name or subgraph_key,
            path=bundle.source_path,
            parent=parent_step_id,
            process=_project_header(
                bundle.spec,
                bundle.source_path,
                bundle.body_markdown,
            ),
        )
    )
    bucket.append(process_node_id)

    step_ids = {step.step_id for step in bundle.plan.steps}

    for dep_name, dep in bundle.plan.deps.items():
        node_id = _dep_node_id(subgraph_key, dep_name)
        nodes.append(
            VizNode(
                id=node_id,
                kind="dep",
                label=dep_name,
                path=dep.path,
                parent=process_node_id,
                dep=_project_dep_details(
                    dep_name,
                    dep,
                    spec=bundle.spec,
                    source_path=bundle.source_path,
                    process_schema_token=bundle.spec.schema_,
                ),
            )
        )
        bucket.append(node_id)
        if dep.produced_by and dep.produced_by in step_ids:
            edges.append(
                VizEdge(
                    source=_step_node_id(subgraph_key, dep.produced_by),
                    target=node_id,
                    kind="produces",
                    label=dep_name,
                )
            )
        for consumer in dep.consumers:
            if consumer in step_ids:
                edges.append(
                    VizEdge(
                        source=_step_node_id(subgraph_key, consumer),
                        target=node_id,
                        kind="consumes",
                        label=dep_name,
                    )
                )

    for step in bundle.plan.steps:
        step_node_id = _step_node_id(subgraph_key, step.step_id)
        node_progress = None
        if progress is not None and step_node_id in progress.nodes:
            node_progress = progress.nodes[step_node_id]
        nodes.append(
            VizNode(
                id=step_node_id,
                kind="step",
                label=step.step_id,
                parent=process_node_id,
                step=_project_step_details(
                    step,
                    bundle.plan.deps,
                    source_path=bundle.source_path,
                    process_schema_token=bundle.spec.schema_,
                ),
                progress=node_progress,
            )
        )
        bucket.append(step_node_id)
        for need in step.needs:
            if need in step_ids:
                edges.append(
                    VizEdge(
                        source=_step_node_id(subgraph_key, need),
                        target=step_node_id,
                        kind="needs",
                    )
                )

        if step.mode == "composite" and step.step_id in bundle.children:
            child_bundle = bundle.children[step.step_id]
            child_key = _child_subgraph_key(subgraph_key, step.step_id)
            _project_bundle(
                bundle=child_bundle,
                parent_step_id=step_node_id,
                subgraph_key=child_key,
                nodes=nodes,
                edges=edges,
                subgraphs=subgraphs,
                progress=progress,
            )


# ── Header projection ───────────────────────────────────────────


def _project_header(spec: ProcessSpec, source_path: str, body_markdown: str) -> ProcessHeader:
    inputs = {name: _project_input(name, decl) for name, decl in spec.inputs.items()}
    outputs = {name: _project_output(name, decl) for name, decl in spec.outputs.items()}
    return ProcessHeader(
        name=spec.name,
        description=spec.description or None,
        process_schema_token=spec.schema_,
        source_path=source_path,
        process_inputs=inputs,
        process_outputs=outputs,
        defaults=_project_defaults(spec.defaults),
        body_markdown=body_markdown,
    )


def _project_input(name: str, decl: ProcessInput) -> InputSpec:
    return InputSpec(
        name=name,
        path=decl.path,
        param=decl.param,
        as_type=_value_type_to_string(decl.as_),
        parse=decl.parse.model_copy() if decl.parse is not None else None,
        required=decl.required,
        default=decl.default,
        description=decl.description,
    )


def _project_output(name: str, decl: ProcessOutput) -> OutputSpec:
    return OutputSpec(
        name=name,
        path=decl.path,
        ref=decl.ref,
        as_type=_value_type_to_string(decl.as_),
        format=decl.format,
        template=decl.template,
        condition=decl.condition,
        description=decl.description,
    )


def _project_defaults(defaults: ProcessDefaults) -> DefaultsBlock:
    adapters = {name: _project_adapter_config(cfg) for name, cfg in defaults.adapters.items()}
    return DefaultsBlock(
        default_adapter=defaults.default_adapter or None,
        adapters=adapters,
        default_execution_profile=defaults.default_execution_profile,
        recommended_execution_profiles=list(defaults.recommended_execution_profiles),
        reuse_policy=defaults.reuse_policy,
        retry=defaults.retry,
    )


def _project_adapter_config(cfg: AdapterConfig) -> AdapterSummary:
    return _adapter_summary(cfg.type, cfg.config)


# ── Step / dep projection ───────────────────────────────────────


def _project_step_details(
    step: ResolvedStep,
    deps: Mapping[str, ResolvedDep],
    *,
    source_path: str,
    process_schema_token: str,
) -> StepDetails:
    reuse: ReusePolicy | None = None
    if step.reuse_policy in _VALID_REUSE_POLICIES:
        reuse = cast("ReusePolicy", step.reuse_policy)
    return StepDetails(
        step_id=step.step_id,
        mode=step.mode,
        description=step.description or None,
        needs=list(step.needs),
        source_path=source_path,
        process_schema_token=process_schema_token,
        handler=step.handler,
        command=step.command,
        uses_path=step.uses_path,
        prompt_paths=list(step.prompt_paths),
        produced_refs=list(step.produced_refs),
        prompt_prefix=step.prompt_prefix,
        inputs=_clone_io_map(step.inputs),
        outputs=_clone_io_map(step.outputs),
        output_root=step.output_root,
        with_=dict(step.with_),  # pyright: ignore[reportCallIssue]  # alias="with" populated by name
        adapter=_project_adapter(step.adapter),
        resources=dict(step.resources),
        variant=step.variant,
        env=dict(step.env),
        max_budget_usd=step.max_budget_usd,
        token_budget=step.token_budget,
        reuse_policy=reuse,
        on_failure=step.on_failure,
        execution_profile=step.execution_profile,
        artifact_namespace=step.artifact_namespace,
        fan_out=_project_fan_out(step.fan_out),
        inputs_summary=_io_summaries(step.inputs, deps),
        outputs_summary=_io_summaries(step.outputs, deps),
    )


def _io_summaries(
    io: Mapping[str, IOSpec],
    deps: Mapping[str, ResolvedDep],
) -> list[IOSummaryEntry]:
    """Compact entries for on-canvas step cards.

    The authored shorthand ``inputs: { items: deps.items }`` resolves to
    an :class:`IOSpec` with the dep's path inlined and no back-reference, so
    the projection reconnects inputs to deps here by name+path: when an
    input's name matches a dep and their paths agree, pull the dep's
    ``as_type`` and ``produced_by``. Entries with no matching dep carry
    whatever the authored ``IOSpec.type`` was.
    """
    entries: list[IOSummaryEntry] = []
    for name, spec in io.items():
        dep = _match_dep_for_io(name, spec, deps)
        as_type = spec.type
        if as_type is None and dep is not None and dep.as_ is not None:
            as_type = _value_type_to_string(dep.as_)
        produced_by = dep.produced_by if dep is not None else None
        entries.append(IOSummaryEntry(name=name, as_type=as_type, produced_by=produced_by))
    return entries


def _match_dep_for_io(
    name: str,
    spec: IOSpec,
    deps: Mapping[str, ResolvedDep],
) -> ResolvedDep | None:
    """Best-effort dep lookup for an IO entry after ref-shorthand resolution."""
    dep = deps.get(name)
    if dep is None:
        return None
    if spec.path is not None and dep.path == spec.path:
        return dep
    if spec.path is None and spec.ref is None:
        return dep
    return None


def _project_dep_details(
    name: str,
    dep: ResolvedDep,
    *,
    spec: ProcessSpec,
    source_path: str,
    process_schema_token: str,
) -> DepDetails:
    parse_dict: dict[str, object] | None = None
    if dep.parse is not None:
        parse_dict = dep.parse.model_dump()
    as_type = _value_type_to_string(dep.as_) if dep.as_ is not None else None
    return DepDetails(
        dep_name=name,
        path=dep.path,
        usage=_compute_dep_usage(name, spec),
        as_type=as_type,
        parse=parse_dict,
        produced_by=dep.produced_by,
        consumers=list(dep.consumers),
        source_path=source_path,
        process_schema_token=process_schema_token,
    )


def _compute_dep_usage(dep_name: str, spec: ProcessSpec) -> list[str]:
    """Return the set of spec keywords that reference ``deps.<dep_name>``.

    Chip labels are the verbatim keyword (``uses``, ``for_each.over``, ``with``,
    ``prompt_paths``) so viz readers see the source language unchanged.
    """
    ref = f"deps.{dep_name}"
    usage: list[str] = []
    for step in spec.steps:
        if step.uses == ref and "uses" not in usage:
            usage.append("uses")
        if step.for_each and step.for_each.over == ref and "for_each.over" not in usage:
            usage.append("for_each.over")
        if step.with_ and ref in step.with_.values() and "with" not in usage:
            usage.append("with")
        if ref in step.prompt_paths and "prompt_paths" not in usage:
            usage.append("prompt_paths")
        for io in list(step.inputs.values()) + list(step.outputs.values()):
            if io.ref == ref and "inputs" not in usage:
                usage.append("inputs")
                break
    return usage


def _project_adapter(adapter: ResolvedAdapter) -> AdapterSummary:
    return _adapter_summary(adapter.type, adapter.config)


def _project_fan_out(fan_out: FanOut | None) -> FanOutDetails | None:
    if fan_out is None:
        return None
    return FanOutDetails(
        over=fan_out.over,
        bind=fan_out.bind,
        source=fan_out.source,
        bind_fields=list(fan_out.bind_fields),
        batch_size=fan_out.batch_size,
        retry=fan_out.retry,
        item_count=len(fan_out.items),
        filtered_count=fan_out.filtered_count,
        align=fan_out.align,
        max_concurrency=fan_out.max_concurrency,
    )


# ── Conversion helpers ──────────────────────────────────────────


def _adapter_summary(type_: str, config: Mapping[str, object]) -> AdapterSummary:
    """Flatten adapter-config keys that carry visual meaning across types."""
    model = _str_or_none(config.get("model"))
    provider = _str_or_none(config.get("provider"))
    tools_raw = config.get("tools")
    tools: list[str] = []
    if isinstance(tools_raw, list):
        tools = [str(t) for t in tools_raw]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    timeout_s = _int_or_none(config.get("timeout_s"))
    max_budget_usd = _float_or_none(config.get("max_budget_usd"))
    return AdapterSummary(
        type=type_,
        model=model,
        provider=provider,
        tools=tools,
        timeout_s=timeout_s,
        max_budget_usd=max_budget_usd,
    )


def _value_type_to_string(vt: ValueType) -> str:
    """Format a :class:`ValueType` back into its authored short form."""
    if vt.kind in {"string", "path"}:
        return vt.kind
    if vt.kind == "list":
        inner = _value_type_to_string(vt.element) if vt.element is not None else "string"
        return f"list<{inner}>"
    if vt.kind == "map":
        key = _value_type_to_string(vt.key) if vt.key is not None else "string"
        value = _value_type_to_string(vt.value) if vt.value is not None else "string"
        return f"map<{key}, {value}>"
    return str(vt.kind)


def _clone_io_map(io: Mapping[str, IOSpec]) -> dict[str, IOSpec]:
    return {k: v.model_copy() for k, v in io.items()}


def _str_or_none(v: object) -> str | None:
    return v if isinstance(v, str) else None


def _int_or_none(v: object) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _float_or_none(v: object) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return float(v)
    return None


# ── Graph helpers ───────────────────────────────────────────────


# Qualified-id helpers live in `metaproc.models.node_ids` so engine/runtime
# code can consume the same contract without reaching into the viz package.
# These underscore-prefixed names remain as in-package aliases so existing
# call sites compile unchanged; new code should import from
# `metaproc.models.node_ids` directly.
from metaproc.models.node_ids import (
    child_subgraph_key as _child_subgraph_key,
)
from metaproc.models.node_ids import (
    dep_node_id as _dep_node_id,
)
from metaproc.models.node_ids import (
    process_node_id as _process_node_id,
)
from metaproc.models.node_ids import (
    step_node_id as _step_node_id,
)

__all_node_id_helpers__ = (
    "_step_node_id",
    "_dep_node_id",
    "_process_node_id",
    "_child_subgraph_key",
)


def _topological_levels(nodes: list[VizNode], edges: list[VizEdge]) -> list[list[str]]:
    """Kahn-style layering over ``needs`` edges among step nodes."""
    step_ids = {n.id for n in nodes if n.kind == "step"}
    incoming: dict[str, set[str]] = {sid: set() for sid in step_ids}
    outgoing: dict[str, set[str]] = {sid: set() for sid in step_ids}
    for e in edges:
        if e.kind != "needs":
            continue
        if e.source in step_ids and e.target in step_ids:
            incoming[e.target].add(e.source)
            outgoing[e.source].add(e.target)

    levels: list[list[str]] = []
    remaining = set(step_ids)
    while remaining:
        current = sorted(sid for sid in remaining if not incoming[sid])
        if not current:
            levels.append(sorted(remaining))
            break
        levels.append(current)
        remaining.difference_update(current)
        for sid in current:
            for downstream in outgoing[sid]:
                incoming[downstream].discard(sid)
    return levels
