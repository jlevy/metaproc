"""Fan-out item discovery from source files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from metaproc.engine.pathing import resolve_item_key
from metaproc.engine.validation import validate_item_outputs
from metaproc.io.frontmatter import extract_items_from_envelope, load_frontmatter_typed
from metaproc.io.state_io import compute_item_dir
from metaproc.models.authored import IOSpec, ProcessStep
from metaproc.models.runtime import get_terminal_statuses

type FilteredFanOutReason = Literal["completed", "cached", "running", "terminal"]


@dataclass(frozen=True)
class FilteredFanOutItem:
    """An authored item context paired with its framework disposition."""

    context: dict[str, str]
    reason: FilteredFanOutReason


@dataclass
class FanOutDiscovery:
    source_path: Path
    item_key: str
    item_fields: list[str]
    actionable_contexts: list[dict[str, str]] = field(default_factory=list)
    filtered_items: list[FilteredFanOutItem] = field(default_factory=list)

    def nonterminal_contexts(self) -> list[dict[str, str]]:
        """Return source-authorized contexts, including reusable in-run items."""
        return [
            *self.actionable_contexts,
            *(item.context for item in self.filtered_items if item.reason != "terminal"),
        ]


def normalize_item_fields(step_def: ProcessStep) -> list[str]:
    """Return the step's declared item fields, preserving authored case."""
    for_each = step_def.for_each
    if not for_each:
        return []
    fields: list[str] = []
    seen: set[str] = set()
    each = for_each.bind.strip()
    for item_field in for_each.bind_fields:
        name = str(item_field).strip()
        if not name or name in seen:
            continue
        fields.append(name)
        seen.add(name)
    if each and each not in seen:
        fields.insert(0, each)
    return fields


def _stringify_item_value(
    value: object, *, source_path: Path, field_name: str, item_label: str
) -> str:
    """Convert a source item value to a runtime string."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str | int | float):
        return str(value)
    msg = (
        f"{source_path}: item '{item_label}' field '{field_name}' must be scalar "
        f"(str/int/float/bool), got {type(value).__name__}"
    )
    raise ValueError(msg)


def discover_items_from_source(
    source_path: Path,
    step_def: ProcessStep,
    *,
    output_paths: dict[str, IOSpec] | None = None,
    params: dict[str, str] | None = None,
    reuse_policy: str | None = "validated_outputs",
    run_dir: Path | None = None,
    expected_run_id: str | None = None,
) -> FanOutDiscovery:
    """Extract actionable item contexts from a fan-out source file.

    ``reuse_policy`` controls how previously-completed items are treated:
    - ``validated_outputs`` (default): re-check that declared outputs exist on disk;
      demote to actionable if any are missing or invalid.
    - ``trust_state``: filter any item with state=completed/cached without re-checking.

    Passing ``None`` (same type as a step's optional ``reuse_policy`` field)
    is treated as trust-state — i.e. do not re-validate — matching the
    pre-existing runtime behavior at call sites that pass the step field
    through without a local default.
    """
    try:
        typed = load_frontmatter_typed(source_path)
    except (ValueError, TypeError, ValidationError) as exc:
        msg = f"{source_path}: failed to validate source frontmatter: {exc}"
        raise ValueError(msg) from exc

    try:
        items = extract_items_from_envelope(typed)
    except TypeError as exc:
        msg = f"{source_path}: {exc}"
        raise TypeError(msg) from exc

    for_each = step_def.for_each
    if for_each is None or not for_each.bind.strip():
        msg = f"{source_path}: fan-out discovery requires step.for_each.bind"
        raise ValueError(msg)
    item_key = for_each.bind.strip()

    item_fields = normalize_item_fields(step_def)
    terminal_statuses = get_terminal_statuses()
    actionable_contexts: list[dict[str, str]] = []
    filtered_items: list[FilteredFanOutItem] = []
    item_key_rows: dict[str, int] = {}

    for index, item in enumerate(items, 1):
        item_label = str(item.get(item_key, f"item#{index}"))

        missing = [f for f in item_fields if item.get(f) in (None, "")]
        if missing:
            msg = f"{source_path}: item '{item_label}' missing required fan-out fields: {', '.join(missing)}"
            raise ValueError(msg)

        context = {
            f: _stringify_item_value(
                item[f], source_path=source_path, field_name=f, item_label=item_label
            )
            for f in item_fields
        }
        item_vars = dict(params or {})
        item_vars.update(context)
        resolved_item_key = resolve_item_key(for_each, item_vars, step_def.id)
        prior_index = item_key_rows.get(resolved_item_key)
        if prior_index is not None:
            raise ValueError(
                f"{source_path}: duplicate for_each.key {resolved_item_key!r} "
                f"for items {prior_index} and {index}"
            )
        item_key_rows[resolved_item_key] = index

        if run_dir is not None:
            from metaproc.engine.pathing import (  # noqa: PLC0415 -- pre-existing local import; needs review
                compute_task_state_dir,
            )
            from metaproc.io.state_io import (  # noqa: PLC0415 -- pre-existing local import; needs review
                read_status_at,
                validate_task_status_identity_at,
            )

            state_dir = compute_task_state_dir(run_dir, step_def, item_vars)
            artifact_dir = compute_item_dir(output_paths or {}, item_vars)
            status_record = read_status_at(state_dir)
            if status_record is not None:
                if expected_run_id is not None:
                    validate_task_status_identity_at(
                        state_dir,
                        status_record,
                        run_id=expected_run_id,
                        step_id=step_def.id,
                        item_key=state_dir.name,
                    )
                if status_record.step_id != step_def.id:
                    actionable_contexts.append(context)
                    continue
                state = status_record.state
                if state in ("completed", "cached"):
                    if (
                        reuse_policy == "validated_outputs"
                        and output_paths
                        and artifact_dir is not None
                    ):
                        output_errors = validate_item_outputs(
                            artifact_dir, output_paths, variables=item_vars
                        )
                        if output_errors:
                            actionable_contexts.append(context)
                            continue
                    filtered_items.append(FilteredFanOutItem(context=dict(context), reason=state))
                    continue
                if state == "failed":
                    # "failed" → retry on next run
                    actionable_contexts.append(context)
                    continue
                if state == "running":
                    # Skip running items — orphaned markers from dead pools
                    # are reconciled to "failed" before discovery runs
                    # (see reconcile_stale_running in state_io.py).
                    filtered_items.append(
                        FilteredFanOutItem(context=dict(context), reason="running")
                    )
                    continue

        status = str(item.get("status", "")).strip().lower()
        if status and status in terminal_statuses:
            filtered_items.append(FilteredFanOutItem(context=dict(context), reason="terminal"))
            continue

        actionable_contexts.append(context)

    return FanOutDiscovery(
        source_path=source_path,
        item_key=item_key,
        item_fields=item_fields,
        actionable_contexts=actionable_contexts,
        filtered_items=filtered_items,
    )
