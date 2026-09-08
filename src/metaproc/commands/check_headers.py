"""metaproc check-headers — validate every frontmatter file in a process tree."""

from __future__ import annotations

from pathlib import Path

import typer
from pydantic import ValidationError

from metaproc.cli import app, get_output
from metaproc.commands.helpers import load_process_spec, resolve_process_path
from metaproc.engine.placeholders import resolve_templates
from metaproc.engine.process_scope import (
    expand_process_vars,
    is_dep_ref,
    parse_dep_ref,
    resolve_process_dep_path,
)
from metaproc.io.frontmatter import load_frontmatter_typed


@app.command("check-headers")
def check_headers(
    process_spec: Path = typer.Argument(..., help="Path to .process.md spec file"),
) -> None:
    """Walk the process tree and validate every frontmatter file."""
    out = get_output()

    errors: list[str] = []
    checked = 0
    seen: set[Path] = set()
    process_path = resolve_process_path(process_spec)

    def check_file(path: Path, label: str) -> None:
        nonlocal checked
        checked += 1
        try:
            load_frontmatter_typed(path)
            out.data(f"  ok  {label}: {path}")
        except (ValueError, TypeError, ValidationError) as e:
            first_line = str(e).splitlines()[0]
            out.data(f"  FAIL  {label}: {path}: {first_line}")
            errors.append(f"{path}: {first_line}")

    def record_missing(path: Path, label: str) -> None:
        out.data(f"  MISSING  {label}: {path}")
        errors.append(f"missing: {path}")

    def walk_process_tree(path: Path, label: str) -> None:
        nonlocal checked
        resolved_path = path.resolve()
        if resolved_path in seen:
            return
        seen.add(resolved_path)

        check_file(path, label)

        try:
            spec = load_process_spec(path)
        except (ValueError, TypeError, ValidationError):
            return

        process_root = path.parent
        process_vars = expand_process_vars(spec, {})

        def _resolve_dep(dep_ref: str) -> Path | None:
            dep_name = parse_dep_ref(dep_ref, context=f"{path} dep ref")
            dep = spec.deps.get(dep_name)
            if dep is None:
                errors.append(f"{path}: unknown dep ref {dep_ref!r}")
                out.data(f"  FAIL  dep: {dep_ref}: unknown dep")
                return None
            return Path(resolve_process_dep_path(dep.path, process_vars, process_root))

        # Walk step.outputs entries; for each entry with a template:, resolve the
        # template path against input defaults and verify it parses. Template
        # paths are resolved the same way dep paths are (see
        # metaproc.engine.process_scope.resolve_process_dep_path): relative to the
        # package root, not the process file's own directory.
        template_vars: dict[str, str] = {
            name: decl.default for name, decl in spec.inputs.items() if decl.default is not None
        }
        template_vars.update(process_vars)
        seen_templates: set[Path] = set()
        for step in spec.steps:
            for io_spec in step.outputs.values():
                if not io_spec.template:
                    continue
                resolved = resolve_templates(io_spec.template, template_vars)
                if "{{" in resolved:
                    # unresolved placeholder — skip this template in check_headers;
                    # full expansion happens in the runtime `validate` command.
                    continue
                tmpl_path = Path(resolve_process_dep_path(resolved, {}, process_root))
                if tmpl_path in seen_templates:
                    continue
                seen_templates.add(tmpl_path)
                if tmpl_path.exists():
                    check_file(tmpl_path, "template")
                else:
                    record_missing(tmpl_path, "template")

        for step in spec.steps:
            for prompt_path in step.prompt_paths:
                if is_dep_ref(prompt_path):
                    resolved_dep = _resolve_dep(prompt_path)
                    if resolved_dep is None:
                        continue
                    runbook_path = resolved_dep
                else:
                    runbook_path = process_root / prompt_path
                if runbook_path.exists():
                    check_file(runbook_path, "runbook")
                else:
                    record_missing(runbook_path, "runbook")

            if step.uses:
                if is_dep_ref(step.uses):
                    resolved_dep = _resolve_dep(step.uses)
                    if resolved_dep is None:
                        continue
                    sub_path = resolved_dep
                else:
                    sub_path = process_root / step.uses
                if sub_path.exists():
                    walk_process_tree(sub_path, "sub-process")
                else:
                    record_missing(sub_path, "sub-process")

    walk_process_tree(process_path, "process")

    out.data(f"\nChecked {checked} files, {len(errors)} error(s)")
    if errors:
        raise typer.Exit(code=1)
