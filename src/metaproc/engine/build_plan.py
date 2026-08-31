"""Plan building — resolves a ProcessSpec into a Plan."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from metaproc.adapters.registry import ADAPTER_REGISTRY, derive_variant, get_adapter
from metaproc.config.env_vars import MetaprocEnv
from metaproc.engine.discovery import discover_items_from_source
from metaproc.engine.graph import validate_step_graph
from metaproc.engine.lane_expand import materialize_execution_lanes
from metaproc.engine.placeholders import resolve_templates
from metaproc.engine.process_scope import (
    is_dep_ref,
    parse_dep_ref,
    resolve_process_dep_path,
)
from metaproc.engine.validation import (
    validate_fan_out_contracts,
    validate_framework_template_names,
    validate_scope_collisions,
)
from metaproc.engine.write_boundary import (
    WriteBoundaryOverlapError,
    find_write_boundary_overlaps,
    validate_agent_write_boundaries,
)
from metaproc.models.authored import (
    AdapterConfig,
    IOSpec,
    ProcessDefaults,
    ProcessSpec,
    ProcessStep,
)
from metaproc.models.execution_profile import (
    ExecutionProfileRegistry,
    ResolvedExecutionProfile,
)
from metaproc.models.plan import FanOut, Plan, ResolvedAdapter, ResolvedDep, ResolvedStep
from metaproc.paths import normalize_path_key
from metaproc.plugins.discovery import get_plugin_registry

log = logging.getLogger(__name__)

_STRICT_ENV_TRUE = frozenset({"1", "true", "yes", "on"})


def _coerce_config_value(key: str, value: str) -> object:
    """Coerce a string config value to the appropriate type."""
    if key in ("timeout_s", "max_turns", "token_budget"):
        return int(value)
    if key == "max_budget_usd":
        return float(value)
    if key in ("verbose", "worktree", "no_session_persistence", "strict_mcp_config"):
        return value.lower() in ("true", "1", "yes")
    if key == "tools":
        return [t.strip() for t in value.split(",") if t.strip()]
    return value


def _find_repo_root(start: Path) -> Path | None:
    """Return the nearest git repo root for registry discovery."""
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        git_marker = candidate / ".git"
        if git_marker.is_dir() or git_marker.is_file():
            return candidate
    return None


def _load_profile_registry(
    process_path: Path,
    profile_files: Sequence[Path],
) -> ExecutionProfileRegistry:
    return ExecutionProfileRegistry.load(
        repo_root=_find_repo_root(process_path),
        explicit_files=tuple(profile_files),
    )


def _resolve_profile(
    registry: ExecutionProfileRegistry,
    profile_name: str | None,
) -> ResolvedExecutionProfile | None:
    if not profile_name:
        return None
    if profile_name not in registry.entries:
        return None
    return registry.resolve(profile_name)


def _resolve_required_profile(
    registry: ExecutionProfileRegistry,
    profile_name: str,
    *,
    context: str,
) -> ResolvedExecutionProfile:
    try:
        return registry.resolve(profile_name)
    except ValueError as exc:
        msg = f"{context}: {exc}"
        raise ValueError(msg) from exc


def _apply_run_namespace_params(
    params: dict[str, str],
    *,
    execution_profile: str | None,
    artifact_namespace: str | None,
) -> dict[str, str]:
    expanded = dict(params)
    if execution_profile:
        expanded["EXECUTION_PROFILE"] = execution_profile
    if artifact_namespace:
        expanded["ARTIFACT_NAMESPACE"] = artifact_namespace
        expanded["VARIANT"] = artifact_namespace
    return expanded


def merge_defaults(step: ProcessStep, defaults: ProcessDefaults) -> dict[str, object]:
    """Merge process-level defaults with step-level overrides."""
    default_adapter_cfg = defaults.default_adapter_config

    default_type = default_adapter_cfg.type
    default_guarantee = default_adapter_cfg.guarantee
    default_config = _resolve_variant_config(
        default_adapter_cfg,
        selector_keys=[defaults.default_adapter, default_type],
    )

    if step.adapter is not None:
        adapter_type = step.adapter.type
        adapter_guarantee = step.adapter.guarantee or default_guarantee
        pa = defaults.adapters.get(adapter_type)
        if pa:
            base_config = _resolve_variant_config(pa, selector_keys=[adapter_type])
        else:
            base_config = dict(default_config)
        base_config.update(_resolve_variant_config(step.adapter, selector_keys=[adapter_type]))
        merged_config: dict[str, object] = base_config
    else:
        adapter_type = default_type
        adapter_guarantee = default_guarantee
        merged_config = dict(default_config)

    merged: dict[str, object] = {}
    merged["adapter_type"] = adapter_type
    merged["adapter_guarantee"] = adapter_guarantee
    merged["adapter"] = adapter_type

    for key, value in merged_config.items():
        if key == "timeout_s":
            merged["timeout"] = value
        else:
            merged[key] = value

    if step.max_budget_usd is not None:
        merged["max_budget_usd"] = step.max_budget_usd
    if step.token_budget is not None:
        merged["token_budget"] = step.token_budget

    merged.setdefault("reuse_policy", defaults.reuse_policy)
    if step.reuse_policy is not None:
        merged["reuse_policy"] = step.reuse_policy

    return merged


def _resolve_variant_config(
    authored: AdapterConfig,
    *,
    selector_keys: Sequence[str | None],
) -> dict[str, object]:
    """Merge shared adapter config with any matching variant-keyed overrides."""
    merged = dict(authored.config)
    for key in selector_keys:
        if not key:
            continue
        overrides = authored.config_by_variant.get(key)
        if overrides:
            merged.update(overrides)
    return merged


def _strict_adapter_validation_enabled() -> bool:
    """Return whether planner-side adapter validation should fail on same-family rejects."""
    return MetaprocEnv.METAPROC_ADAPTER_STRICT.read_bool(default=False)


def _apply_adapter_validation(
    *,
    adapter_type: str,
    authored_adapter_type: str,
    merged_config: dict[str, object],
) -> dict[str, object]:
    """Validate merged config against the resolved adapter and drop or raise on rejects."""
    adapter = get_adapter(adapter_type)
    validate_config = getattr(adapter, "validate_config", None)
    if validate_config is None:
        return merged_config

    rejections = validate_config(merged_config)
    if not rejections:
        return merged_config

    details = "; ".join(f"{rejection.key}: {rejection.reason}" for rejection in rejections)
    same_family = adapter_type == authored_adapter_type
    if not same_family or _strict_adapter_validation_enabled():
        raise ValueError(f"invalid adapter config for {adapter_type}: {details}")

    sanitized = dict(merged_config)
    for rejection in rejections:
        sanitized.pop(rejection.key, None)
    log.warning(
        "invalid adapter config for %s; dropped rejected keys from same-family config: %s",
        adapter_type,
        details,
    )
    return sanitized


def _parse_symbolic_ref(ref: str, *, context: str) -> tuple[str, str]:
    """Parse ``<step-id>.<output-name>`` refs used by step inputs/process outputs."""
    step_id, sep, output_name = ref.partition(".")
    if not sep or not step_id or not output_name:
        msg = f"{context}: invalid ref {ref!r} (expected '<step-id>.<output-name>')"
        raise ValueError(msg)
    return step_id, output_name


def _parse_produced_by(ref: str, *, context: str) -> tuple[str, str | None]:
    step_id, sep, output_name = ref.partition(".")
    if not step_id:
        msg = f"{context}: invalid produced_by {ref!r}"
        raise ValueError(msg)
    return step_id, output_name if sep else None


def _apply_output_root(
    output_root: str | None,
    path_template: str | None,
    params: dict[str, str],
) -> str | None:
    """Prefix relative output paths with the authored ``output_root``."""
    if not output_root or not path_template:
        return path_template
    resolved_path = resolve_templates(path_template, params)
    if Path(resolved_path).is_absolute():
        return path_template
    return str(Path(output_root) / path_template)


def _resolve_step_outputs(
    step: ProcessStep,
    params: dict[str, str],
) -> dict[str, IOSpec]:
    """Resolve authored outputs to plan-time concrete path templates."""
    resolved: dict[str, IOSpec] = {}
    for name, io_spec in step.outputs.items():
        path_template = _apply_output_root(step.output_root, io_spec.path, params)
        resolved_path = resolve_templates(path_template, params) if path_template else None
        resolved[name] = io_spec.model_copy(update={"path": resolved_path})
    return resolved


def _validate_io_ref_compatibility(
    consumer: IOSpec,
    producer: IOSpec,
    *,
    step_id: str,
    input_name: str,
    ref: str,
) -> None:
    """Reject obvious contract mismatches when binding a symbolic ref."""
    if consumer.type and producer.type and consumer.type != producer.type:
        msg = (
            f"step '{step_id}' input '{input_name}': ref {ref!r} type mismatch "
            f"({consumer.type!r} != {producer.type!r})"
        )
        raise ValueError(msg)
    if consumer.kind and producer.kind and consumer.kind != producer.kind:
        msg = (
            f"step '{step_id}' input '{input_name}': ref {ref!r} kind mismatch "
            f"({consumer.kind!r} != {producer.kind!r})"
        )
        raise ValueError(msg)


def _resolve_step_inputs(
    step: ProcessStep,
    params: dict[str, str],
    *,
    resolved_deps: dict[str, ResolvedDep],
    step_outputs: dict[str, dict[str, IOSpec]],
) -> tuple[dict[str, IOSpec], list[str]]:
    """Resolve step inputs, expanding symbolic refs into concrete producer paths."""
    resolved: dict[str, IOSpec] = {}
    ref_needs: list[str] = []

    for input_name, io_spec in step.inputs.items():
        if io_spec.ref:
            if is_dep_ref(io_spec.ref):
                dep_name = parse_dep_ref(
                    io_spec.ref,
                    context=f"step '{step.id}' input '{input_name}'",
                )
                dep = resolved_deps.get(dep_name)
                if dep is None:
                    msg = (
                        f"step '{step.id}' input '{input_name}': ref {io_spec.ref!r} "
                        "points to an unknown dep"
                    )
                    raise ValueError(msg)
                resolved[input_name] = io_spec.model_copy(
                    update={
                        "path": dep.path,
                        "ref": None,
                        "format": io_spec.format,
                    }
                )
                continue
            producer_step_id, output_name = _parse_symbolic_ref(
                io_spec.ref,
                context=f"step '{step.id}' input '{input_name}'",
            )
            producer_outputs = step_outputs.get(producer_step_id)
            if producer_outputs is None:
                msg = (
                    f"step '{step.id}' input '{input_name}': ref {io_spec.ref!r} "
                    "points to an unknown step"
                )
                raise ValueError(msg)
            producer_output = producer_outputs.get(output_name)
            if producer_output is None:
                available = ", ".join(sorted(producer_outputs)) or "<none>"
                msg = (
                    f"step '{step.id}' input '{input_name}': ref {io_spec.ref!r} "
                    f"points to an unknown output (available: {available})"
                )
                raise ValueError(msg)
            _validate_io_ref_compatibility(
                io_spec,
                producer_output,
                step_id=step.id,
                input_name=input_name,
                ref=io_spec.ref,
            )
            resolved[input_name] = io_spec.model_copy(
                update={
                    "path": producer_output.path,
                    "ref": None,
                    "kind": io_spec.kind or producer_output.kind,
                    "type": io_spec.type or producer_output.type,
                    "format": io_spec.format or producer_output.format,
                    "contract": io_spec.contract or producer_output.contract,
                }
            )
            ref_needs.append(producer_step_id)
            continue

        resolved_path = resolve_templates(io_spec.path, params) if io_spec.path else None
        resolved[input_name] = io_spec.model_copy(update={"path": resolved_path})

    return resolved, ref_needs


def _resolve_fan_out_source(
    step: ProcessStep,
    resolved_inputs: dict[str, IOSpec],
    *,
    params: dict[str, str],
    resolved_deps: dict[str, ResolvedDep],
) -> str:
    """Resolve the concrete source path for a fan-out step."""
    if step.for_each is None:
        raise ValueError(f"step '{step.id}': internal error, fan-out is not declared")

    input_name = step.for_each.over
    if is_dep_ref(input_name):
        dep_name = parse_dep_ref(input_name, context=f"step '{step.id}' for_each.over")
        dep = resolved_deps.get(dep_name)
        if dep is None:
            raise ValueError(f"step '{step.id}': unknown dep in for_each.over: {input_name}")
        return dep.path
    source_spec = resolved_inputs.get(input_name)
    if source_spec is None or not source_spec.path:
        raise ValueError(
            f"step '{step.id}': for_each.over '{input_name}' must resolve to an input path"
        )
    return resolve_templates(source_spec.path, params)


def _collect_dep_consumers(spec: ProcessSpec) -> dict[str, list[str]]:
    consumers: dict[str, set[str]] = {}

    def _record(dep_name: str, step_id: str) -> None:
        consumers.setdefault(dep_name, set()).add(step_id)

    for step in spec.steps:
        if step.uses and is_dep_ref(step.uses):
            _record(parse_dep_ref(step.uses, context=f"step '{step.id}' uses"), step.id)
        if step.for_each is not None and is_dep_ref(step.for_each.over):
            _record(
                parse_dep_ref(step.for_each.over, context=f"step '{step.id}' for_each.over"),
                step.id,
            )
        for prompt_path in step.prompt_paths:
            if is_dep_ref(prompt_path):
                _record(
                    parse_dep_ref(prompt_path, context=f"step '{step.id}' prompt_paths"), step.id
                )
        for io_spec in step.inputs.values():
            if io_spec.ref and is_dep_ref(io_spec.ref):
                _record(parse_dep_ref(io_spec.ref, context=f"step '{step.id}' input ref"), step.id)

    return {name: sorted(step_ids) for name, step_ids in consumers.items()}


def _resolve_process_deps(
    spec: ProcessSpec,
    params: dict[str, str],
    *,
    process_dir: Path,
    step_outputs: dict[str, dict[str, IOSpec]],
) -> dict[str, ResolvedDep]:
    consumers = _collect_dep_consumers(spec)
    resolved: dict[str, ResolvedDep] = {}

    for dep_name, dep in spec.deps.items():
        dep_path = resolve_process_dep_path(dep.path, params, process_dir)
        produced_by = dep.produced_by

        # Note: .process.md validation for child-process deps is handled where deps are
        # actually consumed as processes — in composite-step `uses:` resolution (see
        # the check at the composite-step assertion site).

        if produced_by is not None:
            producer_step_id, producer_output_name = _parse_produced_by(
                produced_by,
                context=f"dep '{dep_name}'",
            )
            producer_outputs = step_outputs.get(producer_step_id)
            if producer_outputs is None:
                raise ValueError(
                    f"dep '{dep_name}': produced_by {produced_by!r} points to an unknown step"
                )
            candidate_outputs: list[tuple[str, IOSpec]]
            if producer_output_name is not None:
                producer_output = producer_outputs.get(producer_output_name)
                if producer_output is None:
                    available = ", ".join(sorted(producer_outputs)) or "<none>"
                    raise ValueError(
                        f"dep '{dep_name}': produced_by {produced_by!r} points to an unknown "
                        f"output (available: {available})"
                    )
                candidate_outputs = [(producer_output_name, producer_output)]
            else:
                candidate_outputs = list(producer_outputs.items())

            dep_key = normalize_path_key(dep_path)
            if not any(
                output.path and normalize_path_key(output.path) == dep_key
                for _, output in candidate_outputs
            ):
                available = ", ".join(
                    f"{producer_step_id}.{name}={io_out.path}" for name, io_out in candidate_outputs
                )
                raise ValueError(
                    f"dep '{dep_name}': path {dep.path!r} does not match produced_by "
                    f"{produced_by!r} output path(s): {available}"
                )

        resolved[dep_name] = ResolvedDep(
            path=dep_path,
            produced_by=produced_by,
            consumers=consumers.get(dep_name, []),
            as_=dep.as_,  # pyright: ignore[reportCallIssue]  # Pydantic populate_by_name: field name accepted at runtime; `as` alias is Python keyword
            parse=dep.parse,
        )

    return resolved


def _resolve_prompt_paths(
    step: ProcessStep,
    *,
    resolved_deps: dict[str, ResolvedDep],
    params: dict[str, str],
) -> list[str]:
    resolved_paths: list[str] = []
    for prompt_path in step.prompt_paths:
        if is_dep_ref(prompt_path):
            dep_name = parse_dep_ref(prompt_path, context=f"step '{step.id}' prompt_paths")
            dep = resolved_deps.get(dep_name)
            if dep is None:
                raise ValueError(
                    f"step '{step.id}': prompt path {prompt_path!r} points to an unknown dep"
                )
            resolved_paths.append(dep.path)
            continue
        resolved_paths.append(resolve_templates(prompt_path, params))
    return resolved_paths


def _resolve_produced_refs(
    step: ProcessStep,
    *,
    resolved_deps: dict[str, ResolvedDep],
    step_outputs: dict[str, dict[str, IOSpec]],
    params: dict[str, str],
) -> list[str]:
    """Return the referenced paths this run produces, for ``produced_refs``.

    Two ways a reference can name a file the run writes rather than an authored input.

    A dep-ref carries ``produced_by``, which says outright that a step writes it.

    A raw path is produced when another step in the same plan declares that exact path
    as an output. The plan holds both facts, so whether the run writes it is decided
    rather than assumed, and a released spec that wires a produced file as a raw path
    is as correct as one that wires it through a dep. This is the common shape: a step
    that stages a source snapshot, then a later step that reads it by path.

    Anything else is an authored input the run does not write, so its bytes stay in the
    fingerprint and a missing file is still the misconfiguration it has always been.

    This takes the opposite position to ``_validate_raw_path_dataflow`` below, which
    rejects the same wiring on ``inputs``. The asymmetry is deliberate and is about what
    released specs already contain: a raw-path ``inputs`` duplicate has always been an
    error, so no spec carries one and the rule can stay strict, while a raw-path
    ``prompt_paths`` duplicate has always been accepted, so specs do carry them and
    rejecting them now would break released processes.
    """
    # Keyed the way every other authored-path comparison in this module is keyed. A
    # doubled slash or a `./` segment must not decide whether a file is produced, and
    # `normalize_path_key` is the one definition those comparisons share.
    produced_keys: set[str] = set()
    for producer_id, outputs in step_outputs.items():
        if producer_id == step.id:
            # A step's own outputs are not inputs to itself; excluding them here keeps a
            # step that reads and rewrites one path from dropping it from its fingerprint.
            continue
        for output_spec in outputs.values():
            if output_spec.path:
                produced_keys.add(normalize_path_key(output_spec.path))

    produced: list[str] = []
    for candidate in (*step.prompt_paths, step.uses):
        if not candidate:
            continue
        if is_dep_ref(candidate):
            dep_name = parse_dep_ref(candidate, context=f"step '{step.id}' produced refs")
            dep = resolved_deps.get(dep_name)
            if dep is not None and dep.produced_by and dep.path:
                produced.append(dep.path)
            continue
        resolved = resolve_templates(candidate, params)
        if normalize_path_key(resolved) in produced_keys:
            produced.append(resolved)
    return list(dict.fromkeys(produced))


def _validate_raw_path_dataflow(
    step: ProcessStep,
    *,
    resolved_inputs: dict[str, IOSpec],
    step_outputs: dict[str, dict[str, IOSpec]],
) -> None:
    """Reject authored raw-path wiring when a symbolic ref is available.

    Only for ``inputs``. ``prompt_paths`` takes the opposite position in
    ``_resolve_produced_refs`` above, and that docstring explains why.
    """
    output_index: dict[str, list[str]] = {}
    for producer_step_id, outputs in step_outputs.items():
        for output_name, output_spec in outputs.items():
            if not output_spec.path:
                continue
            output_index.setdefault(output_spec.path, []).append(
                f"{producer_step_id}.{output_name}"
            )

    for input_name, resolved_input in resolved_inputs.items():
        authored_input = step.inputs[input_name]
        if authored_input.ref or not resolved_input.path:
            continue
        matches = output_index.get(resolved_input.path, [])
        matches = [match for match in matches if not match.startswith(f"{step.id}.")]
        if not matches:
            continue
        refs = ", ".join(sorted(matches))
        raise ValueError(
            f"step '{step.id}' input '{input_name}': raw path duplicates declared output(s) "
            f"{refs}; use 'ref:' instead of 'path:'"
        )


def _validate_process_output_refs(
    spec: ProcessSpec,
    *,
    step_outputs: dict[str, dict[str, IOSpec]],
) -> None:
    """Validate process-level output re-exports against resolved step outputs."""
    for output_name, decl in spec.outputs.items():
        if not decl.ref:
            continue
        step_id, step_output_name = _parse_symbolic_ref(
            decl.ref,
            context=f"process output '{output_name}'",
        )
        producer_outputs = step_outputs.get(step_id)
        if producer_outputs is None:
            msg = f"process output '{output_name}': ref {decl.ref!r} points to an unknown step"
            raise ValueError(msg)
        if step_output_name not in producer_outputs:
            available = ", ".join(sorted(producer_outputs)) or "<none>"
            msg = (
                f"process output '{output_name}': ref {decl.ref!r} points to an unknown output "
                f"(available: {available})"
            )
            raise ValueError(msg)


def build_plan(
    spec: ProcessSpec,
    params: dict[str, str],
    *,
    process_path: Path,
    adapter_override: str | None = None,
    artifact_namespace: str | None = None,
    profile_files: Sequence[Path] = (),
    step_profile_overrides: dict[str, str] | None = None,
    config_overrides: dict[str, str] | None = None,
    validate_required_inputs: bool = True,
    validate_spec: bool = True,
) -> Plan:
    """Build a resolved plan from a process spec and parameter values.

    *params* must be pre-expanded via ``expand_process_vars``.

    When *validate_spec* is False, the four spec-level validators (fan-out
    contracts, framework template names, scope collisions, agent write
    boundaries) still run but their errors are ignored. Callers that only
    need the resolved plan shape — e.g. viz — can use this to render authored
    specs that would otherwise fail validation.
    """

    profile_registry = _load_profile_registry(process_path, profile_files)
    if adapter_override is not None:
        run_profile = _resolve_profile(profile_registry, adapter_override)
    elif spec.defaults.default_execution_profile:
        run_profile = _resolve_required_profile(
            profile_registry,
            spec.defaults.default_execution_profile,
            context="defaults.default_execution_profile",
        )
    else:
        run_profile = None
    run_execution_profile = run_profile.name if run_profile is not None else adapter_override
    explicit_artifact_namespace = (
        artifact_namespace or params.get("ARTIFACT_NAMESPACE") or params.get("VARIANT")
    )
    raw_adapter_namespace: str | None = None
    if (
        explicit_artifact_namespace is None
        and run_profile is None
        and adapter_override in ADAPTER_REGISTRY
    ):
        raw_adapter_config = {
            key: _coerce_config_value(key, value) for key, value in (config_overrides or {}).items()
        }
        raw_adapter_namespace = derive_variant(adapter_override, raw_adapter_config)
    run_artifact_namespace = explicit_artifact_namespace or (
        run_execution_profile if run_profile is not None else raw_adapter_namespace
    )
    params = _apply_run_namespace_params(
        params,
        execution_profile=run_execution_profile,
        artifact_namespace=run_artifact_namespace,
    )
    step_profile_overrides = step_profile_overrides or {}

    fan_out_errors = validate_fan_out_contracts(spec, process_path)
    if validate_spec and fan_out_errors:
        msg = "invalid fan-out contracts:\n" + "\n".join(f"  - {e}" for e in fan_out_errors)
        raise ValueError(msg)

    framework_template_errors = validate_framework_template_names(spec)
    if validate_spec and framework_template_errors:
        msg = "invalid framework template names:\n" + "\n".join(
            f"  - {e}" for e in framework_template_errors
        )
        raise ValueError(msg)

    scope_errors = validate_scope_collisions(spec)
    if validate_spec and scope_errors:
        msg = "invalid scope collisions:\n" + "\n".join(f"  - {e}" for e in scope_errors)
        raise ValueError(msg)

    if validate_required_inputs:
        for name, decl in spec.inputs.items():
            if decl.param is None:
                continue
            if decl.required and name not in params and decl.param not in params:
                msg = f"missing required input: {name}"
                raise ValueError(msg)
            value = params.get(name) or params.get(decl.param, "")
            if decl.required and not str(value).strip():
                msg = f"blank required input: {name}"
                raise ValueError(msg)

    if adapter_override is not None and run_profile is None:
        # Accept both raw adapter types ("pi-cli") and named adapter configs
        # from the spec's adapters map ("pi-deepseek-v3.2").
        if adapter_override not in spec.defaults.adapters:
            if adapter_override not in ADAPTER_REGISTRY:
                supported = ", ".join(sorted(ADAPTER_REGISTRY))
                msg = f"unknown adapter override: {adapter_override!r} (supported: {supported})"
                raise ValueError(msg)

    now = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S")
    step_outputs = {step.id: _resolve_step_outputs(step, params) for step in spec.steps}
    boundary_errors = validate_agent_write_boundaries(spec, params, step_outputs=step_outputs)
    if validate_spec and boundary_errors:
        overlaps = find_write_boundary_overlaps(spec, params, step_outputs=step_outputs)
        if overlaps:
            raise WriteBoundaryOverlapError(overlaps)
        msg = "invalid agent write boundaries:\n" + "\n".join(f"  - {e}" for e in boundary_errors)
        raise ValueError(msg)

    resolved_deps = _resolve_process_deps(
        spec,
        params,
        process_dir=process_path.parent,
        step_outputs=step_outputs,
    )
    _validate_process_output_refs(spec, step_outputs=step_outputs)

    resolved_steps: list[ResolvedStep] = []
    for step in spec.steps:
        if (
            step.mode == "composite"
            and step.for_each is not None
            and step.for_each.retry is not None
        ):
            raise ValueError(
                f"step '{step.id}': mapped composite does not support for_each.retry; "
                "declare retries on child leaves and resume the failed item scope"
            )

        step_profile_override = step_profile_overrides.get(step.id)
        if step_profile_override:
            step_profile = _resolve_required_profile(
                profile_registry,
                step_profile_override,
                context=f"--step-variant {step.id}",
            )
        elif step.execution_profile:
            step_profile = _resolve_required_profile(
                profile_registry,
                step.execution_profile,
                context=f"step {step.id!r} execution_profile",
            )
        else:
            step_profile = run_profile
        step_execution_profile = step_profile.name if step_profile is not None else None
        step_artifact_namespace = (
            resolve_templates(step.artifact_namespace, params)
            if step.artifact_namespace
            else run_artifact_namespace
        )
        step_resources: dict[str, object] = {}

        if step_profile is not None:
            adapter_type = cast("str", step_profile.profile.adapter)
            authored_adapter_type = adapter_type
            base_config = dict(step_profile.profile.config)
            step_resources = step_profile.profile.resources.model_dump(exclude_none=True)
            if step.adapter is not None:
                base_config.update(
                    _resolve_variant_config(
                        step.adapter,
                        selector_keys=[step.adapter.type, adapter_type],
                    )
                )
        else:
            authored_adapter_type = (
                step.adapter.type
                if step.adapter is not None
                else spec.defaults.default_adapter_config.type
            )
            if adapter_override is not None:
                effective_adapter_type = adapter_override
            elif step.adapter is not None:
                effective_adapter_type = step.adapter.type
            else:
                effective_adapter_type = spec.defaults.default_adapter_config.type

            per_adapter = spec.defaults.adapters.get(effective_adapter_type)
            if per_adapter is not None:
                base_config = _resolve_variant_config(
                    per_adapter,
                    selector_keys=[effective_adapter_type, per_adapter.type],
                )
                adapter_type = per_adapter.type or effective_adapter_type
            else:
                default_type = spec.defaults.default_adapter_config.type
                if adapter_override is not None and adapter_override != default_type:
                    # Override targets a different adapter type not declared in the
                    # adapters map — do NOT inherit config from an incompatible adapter.
                    # Start with empty config so the target adapter uses its own defaults.
                    base_config = {}
                    adapter_type = adapter_override
                    log.warning(
                        "--adapter %s is not declared in defaults.adapters and differs "
                        "from default adapter %r; using empty base config "
                        "(declare it in adapters map for full config control)",
                        adapter_override,
                        default_type,
                    )
                else:
                    base_config = _resolve_variant_config(
                        spec.defaults.default_adapter_config,
                        selector_keys=[spec.defaults.default_adapter, default_type],
                    )
                    adapter_type = default_type

            if step.adapter is not None:
                selector_keys = (
                    [adapter_override, adapter_type]
                    if adapter_override is not None
                    else [step.adapter.type]
                )
                base_config.update(
                    _resolve_variant_config(
                        step.adapter,
                        selector_keys=selector_keys,
                    )
                )
                if adapter_override is None:
                    adapter_type = step.adapter.type

            if adapter_override is not None:
                # If the override names a config in the adapters map, use its
                # resolved type (e.g. "pi-deepseek-v3.2" → "pi-cli").
                # Otherwise use the override as-is (it's a raw adapter type).
                if per_adapter is not None:
                    adapter_type = per_adapter.type or adapter_override
                else:
                    adapter_type = adapter_override

        merged_config = base_config
        if step.tools:
            merged_config["tools"] = list(step.tools)
        if step.optional_tools:
            merged_config["optional_tools"] = list(step.optional_tools)
        if step.timeout_s is not None:
            merged_config["timeout_s"] = step.timeout_s
        if config_overrides:
            for key, value in config_overrides.items():
                merged_config[key] = _coerce_config_value(key, value)

        merged_config = _apply_adapter_validation(
            adapter_type=adapter_type,
            authored_adapter_type=authored_adapter_type,
            merged_config=merged_config,
        )

        for transform in get_plugin_registry().adapter_config_transforms:
            merged_config = transform(step, merged_config, params)

        resolved_adapter = ResolvedAdapter(type=adapter_type, config=merged_config)

        prompt = resolve_templates(step.prompt_prefix, params) if step.prompt_prefix else None
        resolved_outputs = step_outputs[step.id]
        resolved_inputs, ref_needs = _resolve_step_inputs(
            step,
            params,
            resolved_deps=resolved_deps,
            step_outputs=step_outputs,
        )
        _validate_raw_path_dataflow(
            step,
            resolved_inputs=resolved_inputs,
            step_outputs=step_outputs,
        )

        fan_out: FanOut | None = None
        if step.for_each is not None:
            resolved_source = _resolve_fan_out_source(
                step,
                resolved_inputs,
                params=params,
                resolved_deps=resolved_deps,
            )
            source_path = Path(resolved_source)
            items: list[dict[str, str]] = []
            filtered_count = 0

            if source_path.exists():
                discovery = discover_items_from_source(
                    source_path,
                    step,
                    output_paths=resolved_outputs or None,
                    params=params,
                    reuse_policy=step.reuse_policy or spec.defaults.reuse_policy,
                )
                items = discovery.actionable_contexts
                filtered_count = len(discovery.filtered_items)

            fan_out = FanOut(
                over=step.for_each.over,
                bind=step.for_each.bind,
                source=resolved_source,
                bind_fields=step.for_each.bind_fields,
                batch_size=step.for_each.batch_size,
                items=items,
                filtered_count=filtered_count,
                retry=(
                    None if step.mode == "composite" else step.for_each.retry or spec.defaults.retry
                ),
                align=step.for_each.align,
                max_concurrency=step.for_each.max_concurrency,
            )

        resolved_env: dict[str, str] = {}
        for env_key, env_val in step.env.items():
            resolved_val = resolve_templates(env_val, params)
            # Preserve explicit empty strings. They let a process neutralize an
            # inherited credential or mode switch without mutating the parent
            # environment. Entries with unresolved placeholders (for example
            # {{VARIANT}} or {{EVENT_ID}}) also remain for per-item resolution.
            resolved_env[env_key] = resolved_val

        uses_path: str | None = None
        if step.mode == "composite":
            if step.uses is None or not is_dep_ref(step.uses):
                raise ValueError(f"step '{step.id}': composite mode requires uses: deps.<name>")
            dep_name = parse_dep_ref(step.uses, context=f"step '{step.id}' uses")
            dep = resolved_deps.get(dep_name)
            if dep is None:
                raise ValueError(f"step '{step.id}': composite dep not found: {step.uses!r}")
            if not Path(dep.path).name.endswith(".process.md"):
                raise ValueError(
                    f"step '{step.id}': composite uses {step.uses!r} but dep path "
                    f"{dep.path!r} does not end in .process.md"
                )
            uses_path = dep.path

        resolved_steps.append(
            ResolvedStep(
                step_id=step.id,
                mode=step.mode,
                description=step.description,
                adapter=resolved_adapter,
                resources=step_resources,
                prompt_prefix=prompt,
                prompt_paths=_resolve_prompt_paths(
                    step,
                    resolved_deps=resolved_deps,
                    params=params,
                ),
                reuse_policy=step.reuse_policy or spec.defaults.reuse_policy,
                fan_out=fan_out,
                handler=step.handler,
                command=step.command,
                with_=dict(step.with_ or {}),  # pyright: ignore[reportCallIssue]  # Pydantic populate_by_name: field name accepted at runtime; `with` alias is Python keyword
                inputs=resolved_inputs,
                outputs=resolved_outputs,
                env=resolved_env,
                needs=list(dict.fromkeys([*step.needs, *ref_needs])),
                on_failure=step.on_failure,
                uses_path=uses_path,
                produced_refs=_resolve_produced_refs(
                    step,
                    resolved_deps=resolved_deps,
                    step_outputs=step_outputs,
                    params=params,
                ),
                execution_profile=step_execution_profile,
                artifact_namespace=step_artifact_namespace,
                variant=step.variant,
                output_root=step.output_root,
                max_budget_usd=step.max_budget_usd,
                token_budget=step.token_budget,
            )
        )

    graph_errors = validate_step_graph(resolved_steps)
    if graph_errors:
        msg = "invalid step dependency graph:\n" + "\n".join(f"  - {e}" for e in graph_errors)
        raise ValueError(msg)

    execution_lanes = materialize_execution_lanes(
        spec.lane_matrix,
        registry=profile_registry,
        fallback_profile=run_profile,
        fallback_artifact_namespace=run_artifact_namespace,
    )

    return Plan(
        generated_at=now,
        process=str(process_path),
        params=dict(params),
        execution_profile=run_execution_profile,
        artifact_namespace=run_artifact_namespace,
        deps=resolved_deps,
        resource_budgets=[budget.model_copy(deep=True) for budget in spec.resource_budgets],
        steps=resolved_steps,
        lane_matrix=spec.lane_matrix,
        execution_lanes=execution_lanes,
    )
