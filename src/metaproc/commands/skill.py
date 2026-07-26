"""``metaproc skill`` — generate and install skill artifacts.

Commands:
- ``metaproc skill <name>`` — print the composed SKILL.md to stdout
- ``metaproc skill --list`` — list all registered skills
- ``metaproc skill <name> --install`` — write SKILL.md to ``.agents/skills/<name>/``
  (portable, cross-agent) and mirror it to ``.claude/skills/<name>/``
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from metaproc.cli import app
from metaproc.skill.compose import compose_skill
from metaproc.skill.registry import get_skill, iter_skills
from metaproc.skill.spec import SkillSpec


@app.command("skill")
def skill_command(
    name: str | None = typer.Argument(None, help="Skill name to compose or install."),
    list_skills: bool = typer.Option(False, "--list", help="List all registered skills."),
    install: bool = typer.Option(
        False,
        "--install",
        help="Write the composed SKILL.md to .agents/skills/<name>/ + mirror to .claude/skills/<name>/.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format (--list only)."),
) -> None:
    """Compose, list, or install generated skill artifacts."""
    if list_skills:
        _handle_list(json_output=json_output)
        return

    if name is None:
        typer.echo("Usage: metaproc skill <name> [--install] or metaproc skill --list", err=True)
        raise typer.Exit(2)

    spec = get_skill(name)
    if spec is None:
        available = [s.name for s in iter_skills()]
        typer.echo(
            f"Unknown skill: {name!r}. Available: {', '.join(available) or '(none)'}.", err=True
        )
        raise typer.Exit(2)

    if install:
        _handle_install(spec)
    else:
        _handle_print(spec)


def _handle_list(*, json_output: bool) -> None:
    """List all registered skills."""
    skills = sorted(iter_skills(), key=lambda s: s.name)
    if not skills:
        typer.echo("No skills registered.", err=True)
        raise typer.Exit(1)

    if json_output:
        import json  # noqa: PLC0415 -- pre-existing local import; needs review

        data = [
            {"name": s.name, "description": s.description, "allowed_tools": s.allowed_tools}
            for s in skills
        ]
        sys.stdout.write(json.dumps(data, indent=2) + "\n")
    else:
        for s in skills:
            # Truncate description at first sentence for compact listing
            short_desc = (
                s.description.split(".")[0] + "." if "." in s.description else s.description
            )
            typer.echo(f"  {s.name:<16} {short_desc}")


def _handle_print(spec: SkillSpec) -> None:
    """Print composed SKILL.md to stdout."""
    composed = compose_skill(spec)
    sys.stdout.write(composed)


def _handle_install(spec: SkillSpec) -> None:
    """Write the composed SKILL.md to the portable ``.agents/skills/<name>/`` path and
    mirror it to ``.claude/skills/<name>/``.

    ``.agents/skills/`` is the cross-agent standard read by Codex, Gemini CLI, Cursor,
    Copilot, and others; Claude Code reads only ``.claude/skills/`` (it does not scan
    ``.agents/``), so the same payload is copied to both — never symlinked.
    """
    composed = compose_skill(spec)
    written: list[str] = []
    for root in (".agents", ".claude"):
        target_dir = Path.cwd() / root / "skills" / spec.name
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / "SKILL.md"
        target_file.write_text(composed)
        written.append(str(target_file))
    typer.echo("Installed:\n  " + "\n  ".join(written))
