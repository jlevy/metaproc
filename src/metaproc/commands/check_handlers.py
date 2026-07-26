"""metaproc check-handlers — resolve every code-mode handler in a process tree.

Walks the process tree from the given .process.md spec (descending into
sub-process ``uses:`` references) and, for each step with ``mode: code`` and a
``handler:`` reference, calls ``resolve_code_handler`` to verify:

- the file or installed module imports cleanly,
- the named function exists and is callable,
- the signature accepts at least two required positional parameters (the
  ``context`` and ``step`` adapter contract).

Steps that use ``command:`` (shell) instead of ``handler:`` are not validated
here — only true code handlers are in scope. ``mode: agent``/``manual``/
``composite`` steps are skipped (composite descents happen via the spec walk).

Exit code 0 if every handler resolves; non-zero with per-failure lines on
stderr otherwise. Distinct from ``metaproc check-headers``, which validates
frontmatter conformance.
"""

from __future__ import annotations

from pathlib import Path

import typer
from pydantic import ValidationError

from metaproc.cli import app, get_output
from metaproc.commands.helpers import load_process_spec, resolve_process_path
from metaproc.engine.code_handler import resolve_code_handler
from metaproc.engine.process_scope import is_dep_ref, parse_dep_ref, resolve_process_dep_path


@app.command("check-handlers")
def check_handlers(
    process_spec: Path = typer.Argument(..., help="Path to .process.md spec file"),
) -> None:
    """Resolve every ``mode: code`` handler reachable from this process tree."""
    out = get_output()

    errors: list[str] = []
    checked = 0
    seen: set[Path] = set()
    process_path = resolve_process_path(process_spec)

    def walk(path: Path, label: str) -> None:
        nonlocal checked
        resolved = path.resolve()
        if resolved in seen:
            return
        seen.add(resolved)

        try:
            spec = load_process_spec(path)
        except (ValueError, TypeError, ValidationError) as e:
            first_line = str(e).splitlines()[0]
            out.data(f"  SKIP  {label}: {path}: invalid spec ({first_line})")
            errors.append(f"{path}: invalid spec — {first_line}")
            return

        process_root = path.parent

        for step in spec.steps:
            if step.mode == "code" and step.handler:
                checked += 1
                try:
                    resolve_code_handler(step.handler, process_root)
                    out.data(f"  ok    handler  {label}:{step.id}: {step.handler}")
                except (
                    ValueError,
                    TypeError,
                    AttributeError,
                    ImportError,
                    FileNotFoundError,
                ) as e:
                    first_line = str(e).splitlines()[0]
                    out.data(f"  FAIL  handler  {label}:{step.id}: {step.handler}: {first_line}")
                    errors.append(f"{path}:{step.id}: {first_line}")

            # Descend into composite sub-processes so nested handlers are
            # checked. Unknown deps and missing sub-process files are
            # **errors**, not skips — a typo in ``uses: deps.foo`` or a
            # missing referenced spec means we cannot verify the nested
            # handlers, and silently exiting 0 makes this command weaker
            # than its name implies.
            if step.uses:
                if is_dep_ref(step.uses):
                    dep_name = parse_dep_ref(step.uses, context=f"{path} step {step.id} uses")
                    dep = spec.deps.get(dep_name)
                    if dep is None:
                        msg = (
                            f"unknown dep '{step.uses}' (declared deps: "
                            f"{sorted(spec.deps.keys()) or 'none'})"
                        )
                        out.data(f"  FAIL  uses     {label}:{step.id}: {msg}")
                        errors.append(f"{path}:{step.id}: {msg}")
                        continue
                    sub_path = Path(resolve_process_dep_path(dep.path, {}, process_root))
                else:
                    sub_path = process_root / step.uses
                if sub_path.exists():
                    walk(sub_path, f"{label}>{step.id}")
                else:
                    msg = f"missing sub-process file: {sub_path}"
                    out.data(f"  FAIL  uses     {label}:{step.id}: {msg}")
                    errors.append(f"{path}:{step.id}: {msg}")

    walk(process_path, "process")

    out.data(f"\nChecked {checked} handler(s), {len(errors)} error(s)")
    if errors:
        raise typer.Exit(code=1)
