"""metaproc compare-matrix — tabular comparison across variant directories."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from metaproc.cli import app, get_output
from metaproc.commands.helpers import dot_get, fmt_value
from metaproc.errors import CLIError, ValidationError
from metaproc.io import FmFormatError, fmf_read_frontmatter
from metaproc.plugins.discovery import get_plugin_registry


def _resolve_compare_settings(
    output_file: str | None,
    fields: str | None,
) -> tuple[str | None, str, list[str]]:
    """Resolve output filename, envelope key, and field list."""
    registry = get_plugin_registry()
    compare_defaults = registry.compare_defaults
    parsed_fields = [field.strip() for field in (fields or "").split(",") if field.strip()]

    envelope_key = Path(output_file).stem if output_file else None
    if envelope_key is None and len(compare_defaults) == 1:
        envelope_key = next(iter(compare_defaults))

    resolved_output = output_file or (f"{envelope_key}.md" if envelope_key else None)
    if resolved_output is None:
        raise CLIError("--output required because no unique plugin compare default is registered")

    if not parsed_fields:
        if envelope_key is not None and envelope_key in compare_defaults:
            parsed_fields = list(compare_defaults[envelope_key])
        elif len(compare_defaults) == 1:
            parsed_fields = list(next(iter(compare_defaults.values())))

    if not parsed_fields:
        raise CLIError("--fields must specify at least one field")

    return envelope_key, resolved_output, parsed_fields


@app.command("compare-matrix")
def compare_matrix(
    process_dir: Path = typer.Argument(..., help="Process dir under a run-id"),
    output_file: str | None = typer.Option(None, "--output", help="Output file basename"),  # noqa: UP007
    fields: str | None = typer.Option(
        None, "--fields", help="Comma-separated frontmatter fields to compare"
    ),  # noqa: UP007
) -> None:
    """Print a comparison matrix of frontmatter across variant directories."""
    out = get_output()

    if not process_dir.exists():
        raise CLIError(f"{process_dir} not found")

    envelope_key, resolved_output_file, field_list = _resolve_compare_settings(output_file, fields)

    # Discover variants: each subdirectory of process_dir is a variant
    variant_data: dict[str, dict[str, dict[str, Any]]] = {}
    for variant_dir in sorted(process_dir.iterdir()):
        if not variant_dir.is_dir() or variant_dir.name.startswith("."):
            continue
        if not any(d.is_dir() for d in variant_dir.iterdir() if not d.name.startswith(".")):
            continue
        variant = variant_dir.name
        variant_items: dict[str, dict[str, Any]] = {}
        for item_dir in sorted(variant_dir.iterdir()):
            if not item_dir.is_dir() or item_dir.name.startswith("."):
                continue
            output_path = item_dir / resolved_output_file
            if output_path.exists():
                try:
                    fm = fmf_read_frontmatter(output_path)
                    if isinstance(fm, dict):
                        if envelope_key and isinstance(fm.get(envelope_key), dict):
                            variant_items[item_dir.name] = fm[envelope_key]
                        else:
                            variant_items[item_dir.name] = fm
                    else:
                        variant_items[item_dir.name] = {"_error": "missing frontmatter"}
                except (FmFormatError, ValueError):
                    variant_items[item_dir.name] = {"_error": "parse error"}
        if variant_items:
            variant_data[variant] = variant_items

    all_items = sorted({t for items in variant_data.values() for t in items})
    all_variants = sorted(variant_data)

    if not all_items:
        raise ValidationError("No items found.")

    field_col_w = max(len(f) for f in field_list)
    col_w = max(max((len(v) for v in all_variants), default=12), field_col_w, 12)

    out.data(
        f"\n{'Item':<8}  {'Field':<{field_col_w}}  "
        + "  ".join(f"{v:^{col_w}}" for v in all_variants)
    )
    out.data(f"{'─' * 8}  {'─' * field_col_w}  " + "  ".join("─" * col_w for _ in all_variants))

    for item in all_items:
        for fi, field_name in enumerate(field_list):
            label = item if fi == 0 else ""
            values: list[str] = []
            for variant in all_variants:
                item_data = variant_data.get(variant, {}).get(item, {})
                val = dot_get(item_data, field_name, "—")
                values.append(fmt_value(val))

            unique = set(values)
            marker = " " if len(unique) <= 1 else "*"
            out.data(
                f"{label:<8}  {field_name:<{field_col_w}}  "
                + "  ".join(f"{v:^{col_w}}" for v in values)
                + f"  {marker}"
            )
        if len(field_list) > 1:
            out.data("")

    out.data(f"\n{len(all_items)} items, {len(all_variants)} variants, {len(field_list)} fields")
