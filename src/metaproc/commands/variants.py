"""metaproc variants — list execution profiles available to a process."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from metaproc.adapters.registry import get_adapter
from metaproc.cli import app, get_output
from metaproc.commands.helpers import load_process_spec, resolve_process_path
from metaproc.models.authored import ProcessSpec
from metaproc.models.execution_profile import ExecutionProfileRegistry, ResolvedExecutionProfile


def _find_repo_root(start: Path) -> Path | None:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def _profile_record(
    profile: ResolvedExecutionProfile,
    *,
    spec: ProcessSpec,
) -> dict[str, object]:
    required, optional = _compatibility(profile, spec)
    auth = _auth_readiness(profile)
    return {
        "profile": profile.name,
        "source": profile.source.kind,
        "source_path": profile.source.path,
        "status": profile.profile.status or "",
        "adapter": profile.profile.adapter or "",
        "provider": profile.profile.provider or profile.profile.config.get("provider", ""),
        "model": profile.profile.model or profile.profile.config.get("model", ""),
        "required": required,
        "optional": optional,
        "auth": auth,
        "resources": profile.profile.resources.model_dump(exclude_none=True),
        "capabilities": profile.profile.capabilities,
        "notes": profile.profile.notes or "",
    }


def _compatibility(profile: ResolvedExecutionProfile, spec: ProcessSpec) -> tuple[str, str]:
    native_tools_raw = profile.profile.capabilities.get("native_tools", [])
    native_tools = (
        {str(tool) for tool in native_tools_raw} if isinstance(native_tools_raw, list) else set()
    )
    required_tools: set[str] = set()
    optional_tools: set[str] = set()
    for step in spec.steps:
        required_tools.update(step.tools)
        optional_tools.update(step.optional_tools)
    if required_tools and not required_tools.issubset(native_tools):
        return "no", "warn" if optional_tools else "yes"
    if optional_tools and not optional_tools.issubset(native_tools):
        return "yes", "warn"
    return "yes", "yes"


def _auth_readiness(profile: ResolvedExecutionProfile) -> str:
    adapter_type = profile.profile.adapter
    if not adapter_type:
        return "unknown"
    try:
        status = get_adapter(adapter_type).check_auth()
    except Exception:  # noqa: BLE001
        return "error"
    if status.cli_found and status.credentials_found:
        return "ok"
    if not status.cli_found:
        return "missing-binary"
    return "missing-credentials"


def _format_table(records: list[dict[str, object]]) -> str:
    columns = [
        ("Profile", "profile", 18),
        ("Source", "source", 10),
        ("Status", "status", 12),
        ("Adapter", "adapter", 16),
        ("Provider", "provider", 14),
        ("Model", "model", 18),
        ("Required", "required", 8),
        ("Optional", "optional", 8),
        ("Auth", "auth", 18),
        ("Notes", "notes", 42),
    ]
    widths = [
        max(width, *(len(str(record.get(key, ""))) for record in records))
        for _title, key, width in columns
    ]
    header = "  ".join(
        title.ljust(widths[index]) for index, (title, _key, _width) in enumerate(columns)
    )
    rows = [header]
    for record in records:
        rows.append(
            "  ".join(
                str(record.get(key, "")).ljust(widths[index])
                for index, (_title, key, _width) in enumerate(columns)
            )
        )
    return "\n".join(rows)


@app.command("variants")
def variants(
    process_spec: Path = typer.Argument(..., help="Path to .process.md spec file"),
    variant_file: list[Path] = typer.Option(
        [],
        "--variant-file",
        help="Additional execution-profile registry file (repeatable).",
    ),
    profile_file: list[Path] = typer.Option(
        [],
        "--profile-file",
        help="Additional execution-profile registry file (repeatable).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON records."),
) -> None:
    """List execution profiles visible to a process."""
    out = get_output()
    process_path = resolve_process_path(process_spec)
    spec = load_process_spec(process_path)
    registry = ExecutionProfileRegistry.load(
        repo_root=_find_repo_root(process_path),
        explicit_files=[*variant_file, *profile_file],
    )
    records = [_profile_record(profile, spec=spec) for profile in registry.list_resolved()]
    if json_output:
        json.dump(records, sys.stdout, default=str)
        sys.stdout.write("\n")
        sys.stdout.flush()
        return
    out.data(_format_table(records))
