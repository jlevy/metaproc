"""Shared CLI helpers used across commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from metaproc.config.env_vars import MetaprocEnv
from metaproc.engine.discovery import discover_items_from_source, normalize_item_fields
from metaproc.engine.placeholders import (
    check_unresolved_placeholders,
    resolve_output_paths,
    resolve_templates,
)
from metaproc.errors import CLIError, ValidationError
from metaproc.io.frontmatter import ProcessEnvelope, load_frontmatter_typed
from metaproc.models.authored import IOSpec, ProcessSpec, ProcessStep
from metaproc.paths import PROCESS_SPEC_SUFFIX
from metaproc.runtime_paths import RunSettingsError, resolve_runtime_runs_dir


def resolve_process_path(process_path: Path) -> Path:
    """Validate and normalize a process spec path.

    Accepts either:

    * A direct ``*.process.md`` file path (canonical form), or
    * A directory containing ``<dirname>.process.md`` (convenience form
      used by playbooks and plan specs, e.g. ``process/predict`` → resolves
      to ``process/predict/predict.process.md``).

    Raises :class:`CLIError` if neither resolves to an existing spec.
    """
    # Convenience form: directory → <dirname>/<dirname>.process.md
    if process_path.is_dir():
        candidate = process_path / f"{process_path.name}{PROCESS_SPEC_SUFFIX}"
        if candidate.exists():
            return candidate
        raise CLIError(
            f"process spec directory has no matching spec file: "
            f"{process_path} (expected {candidate})"
        )

    if not process_path.name.endswith(PROCESS_SPEC_SUFFIX):
        raise CLIError(f"process spec path must end in {PROCESS_SPEC_SUFFIX}: {process_path}")
    if not process_path.exists():
        raise CLIError(f"process spec not found: {process_path}")
    return process_path


def load_process_spec(path: Path) -> ProcessSpec:
    """Load and validate a ``.process.md`` file."""
    typed = load_frontmatter_typed(path)
    if not isinstance(typed, ProcessEnvelope):
        msg = f"{path}: expected a process specification"
        raise ValidationError(msg)
    return typed.process


def parse_var_args(var: list[str]) -> dict[str, str]:
    """Parse KEY=VALUE arguments into a dict."""
    variables: dict[str, str] = {}
    for v in var:
        if "=" not in v:
            raise ValidationError(f"variable must be KEY=VALUE, got: {v}")
        key, value = v.split("=", 1)
        variables[key] = value
    return variables


def seed_runtime_vars(variables: dict[str, str]) -> dict[str, str]:
    """Populate implicit runtime vars from env when CLI args omitted them.

    ``RUNS_DIR`` is used widely in path templates and code-step context, but
    cloud entrypoints historically relied on the child process inheriting it
    from ``os.environ``. Seeding it into the explicit variable map makes path
    resolution and handler context deterministic across local and cloud runs.
    The seeded value is always absolute.
    """
    try:
        explicit = variables.get("RUNS_DIR")
        runs_dir = resolve_runtime_runs_dir(explicit)
    except RunSettingsError as exc:
        raise ValidationError(str(exc)) from exc
    if runs_dir is not None:
        variables["RUNS_DIR"] = str(runs_dir)
    return variables


def require_runtime_runs_dir(variables: dict[str, str], *, command: str) -> None:
    """Require ``RUNS_DIR`` after runtime variable seeding and input defaults."""
    try:
        runs_dir = resolve_runtime_runs_dir(variables.get("RUNS_DIR"), required=True)
    except RunSettingsError as exc:
        raise ValidationError(f"{command}: {exc}") from exc
    variables["RUNS_DIR"] = str(runs_dir)


def validate_gcp_worker_topology(
    backend: str,
    *,
    cloud: bool = False,
    dry_run: bool = False,
    batch_task_index: str | None,
    orchestrator_marker: str | None = None,
    command: str = "run-process",
) -> None:
    """Reject operator-host execution through the internal GCP worker backend."""
    admitted_orchestrator = orchestrator_marker == "1" or bool(
        batch_task_index and batch_task_index.strip()
    )
    if backend == "gcp-worker" and not cloud and not dry_run and not admitted_orchestrator:
        if command == "run-parallel":
            raise CLIError(
                "run-parallel --backend gcp-worker is only supported inside the GCP Batch "
                "orchestrator. Start a full-cloud run with "
                "run-process <spec> --backend gcp-worker --cloud."
            )
        raise CLIError(
            "--backend gcp-worker without --cloud is only supported inside the GCP Batch "
            "orchestrator. Use run-process <spec> --backend gcp-worker --cloud for a "
            "full-cloud run."
        )


def resolve_gcp_worker_runs_dir(backend: str) -> str:
    """Return the shared cloud runs directory for the GCP worker backend only."""
    if backend != "gcp-worker":
        return ""
    if not MetaprocEnv.METAPROC_GCP_FILESTORE_SERVER.read_str(default=""):
        return ""
    mount_path = MetaprocEnv.METAPROC_GCP_FILESTORE_MOUNT_PATH.read_str(default="/mnt/filestore")
    return str(Path(mount_path) / "runs")


def parse_adapter_config(raw: list[str]) -> dict[str, str]:
    """Parse adapter config KEY=VALUE arguments."""
    config: dict[str, str] = {}
    for ac in raw:
        if "=" not in ac:
            raise ValidationError(f"adapter-config must be KEY=VALUE, got: {ac}")
        key, value = ac.split("=", 1)
        config[key] = value
    return config


def parse_step_variant_args(raw: list[str]) -> dict[str, str]:
    """Parse STEP=PROFILE arguments for step-level execution-profile overrides."""
    overrides: dict[str, str] = {}
    for value in raw:
        if "=" not in value:
            raise ValidationError(f"step-variant must be STEP=PROFILE, got: {value}")
        step_id, profile = value.split("=", 1)
        if not step_id.strip() or not profile.strip():
            raise ValidationError(f"step-variant must be STEP=PROFILE, got: {value}")
        overrides[step_id.strip()] = profile.strip()
    return overrides


def load_item_contexts() -> list[dict[str, str]]:
    """Load worker item contexts from env or a spill file."""
    contexts_json = MetaprocEnv.METAPROC_ITEM_CONTEXTS.read_str(default="")
    if contexts_json:
        return cast("list[dict[str, str]]", json.loads(contexts_json))

    contexts_path = MetaprocEnv.METAPROC_ITEM_CONTEXTS_FILE.read_str(default="")
    if contexts_path:
        path = Path(contexts_path)
        if not path.exists():
            raise CLIError(f"METAPROC_ITEM_CONTEXTS_FILE not found: {path}")
        return cast("list[dict[str, str]]", json.loads(path.read_text()))

    return []


def find_step_def(spec: ProcessSpec, step_id: str) -> ProcessStep:
    """Find a step by id in the spec's steps list."""
    for step in spec.steps:
        if step.id == step_id:
            return step
    available = [s.id for s in spec.steps]
    msg = f"step '{step_id}' not found. Available: {', '.join(available)}"
    raise CLIError(msg)


def enforce_no_unresolved_placeholders(
    text: str,
    *,
    context: str = "",
    allowed: set[str] | None = None,
) -> None:
    """Raise ValidationError if *text* contains unresolved placeholders."""
    errors = check_unresolved_placeholders(text, context=context, allowed=allowed)
    if errors:
        msg = "unresolved placeholders: " + "; ".join(errors)
        raise ValidationError(msg)


def resolve_record_output_paths(
    outputs: dict[str, IOSpec], variables: dict[str, str]
) -> dict[str, str]:
    """Resolve output spec templates to concrete paths for state records."""
    return resolve_output_paths(outputs, variables)


def enrich_single_item(
    step_id: str,
    step_def: ProcessStep,
    item_value: str,
    variables: dict[str, str],
    resolved_inputs: dict[str, IOSpec] | None = None,
    resolved_source: str | None = None,
) -> dict[str, str]:
    """Look up enriched context for a single ``for_each`` item.

    Tries ``METAPROC_ITEM_CONTEXTS`` env var first, then falls back to
    the step input referenced by ``for_each.over``.

    Returns a dict of ``{field_name: value}`` for all ``for_each`` fields.
    """

    for_each = step_def.for_each
    if not for_each:
        raise CLIError(f"step '{step_id}' does not declare for_each")

    each = for_each.bind
    item_fields = normalize_item_fields(step_def)
    extra_fields = [f for f in item_fields if f != each]

    if not extra_fields:
        return {each: item_value}

    # Try worker payload first (inline env var or spilled file).
    all_contexts = load_item_contexts()
    if all_contexts:
        matched = [ctx for ctx in all_contexts if ctx.get(each) == item_value]
        if matched:
            return matched[0]

    # Fall back to the resolved source input.
    input_name = for_each.over
    source = resolved_source
    if not source:
        source_spec = (resolved_inputs or step_def.inputs).get(input_name)
        source = source_spec.path if source_spec else None
    if not source:
        raise CLIError(
            f"step '{step_id}' requires item fields {', '.join([each, *extra_fields])}; "
            f"--item only supplies the primary key and no resolved for_each.over input is "
            f"available "
            f"for enrichment. Pass the extra fields via --var or use run-parallel."
        )
    resolved_source = resolve_templates(source, variables)
    source_path = Path(resolved_source)
    if not source_path.exists():
        raise CLIError(f"source file not found: {source_path}")

    discovery = discover_items_from_source(source_path, step_def, params=variables)
    # Search all items (actionable + filtered) since user explicitly chose this item.
    all_item_contexts = discovery.actionable_contexts + [
        item.context for item in discovery.filtered_items
    ]
    matched = [ctx for ctx in all_item_contexts if ctx.get(each) == item_value]
    if not matched:
        raise CLIError(f"item '{item_value}' not found in source for step '{step_id}'")
    return matched[0]


_SENTINEL = object()


def dot_get(data: dict[str, Any], dotted_key: str, default: object = None) -> object:
    """Resolve a dot-notation key against a nested dict."""
    current: Any = data
    for part in dotted_key.split("."):
        if not isinstance(current, dict):
            return default
        current = cast("dict[str, Any]", current).get(part, _SENTINEL)
        if current is _SENTINEL:
            return default
    return current


def fmt_value(value: object) -> str:
    """Format a value for display — returns ``'N/A'`` for None."""
    if value is None:
        return "N/A"
    return str(value)


def relpath(p: Path) -> Path:
    """Return *p* relative to CWD so paths stay portable."""
    try:
        return p.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        return p
