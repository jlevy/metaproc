"""metaproc env — inspect the MetaprocEnv registry.

Lists every registered env var with its kind, current value, and
one-line description. Secrets are redacted (length only). This is the
canonical reference for what operators need to set in ``.env``.

``--template`` regenerates ``.env.example`` from the registry; the
golden test ``test_env_template.py`` enforces that the checked-in
``.env.example`` stays in sync.
"""

from __future__ import annotations

import textwrap
from typing import cast, get_args

import typer

from metaproc.cli import app, get_output
from metaproc.config.env_enum import EnvKind
from metaproc.config.env_vars import MetaprocEnv
from metaproc.output import OutputFormat

_KIND_ORDER: dict[EnvKind, int] = {"REAL": 0, "SECRET": 1, "TUNABLE": 2, "OPTIONAL": 3}

_TEMPLATE_HEADER = """\
# ─────────────────────────────────────────────────────────────
# .env.example — generated from the MetaprocEnv registry.
#
# DO NOT EDIT BY HAND. To regenerate after a registry change:
#   uv --config-file uv.toml run --frozen metaproc env --template > .env.example
# CI gates this file via tests/test_env_template.py.
#
# Copy to .env (gitignored) and replace placeholder values.
# metaproc auto-loads .env on CLI bootstrap, so every variable here is
# picked up by every `metaproc` command.
#
# Legend:
#   REAL      — real default for this project, copy as-is
#   SECRET    — illustration only, MUST be replaced with a real secret
#   TUNABLE   — sensible default, change only if your situation requires it
#   OPTIONAL  — commented out by default; uncomment and set if needed
# ─────────────────────────────────────────────────────────────
"""


def render_env_template() -> str:
    """Render the canonical ``.env.example`` from :class:`MetaprocEnv`.

    Members are emitted in registry source order so the file mirrors the
    domain grouping (GCP infra → repo resolution → arena → secrets → ...).
    OPTIONAL members without an example value are skipped (a commented-out
    ``export NAME=`` is just noise). SECRETs without an explicit example
    fall back to ``REPLACE_WITH_<NAME>`` so operators see a clear marker.
    """
    out: list[str] = [_TEMPLATE_HEADER]
    for member in MetaprocEnv:
        kind = member.kind
        example = member.example
        if kind == "OPTIONAL" and not example:
            continue
        if kind == "SECRET" and not example:
            example = f"REPLACE_WITH_{member.name}"
        comment_body = f"{kind} — {member.description}"
        for line in textwrap.wrap(comment_body, width=78, break_long_words=False):
            out.append(f"# {line}")
        prefix = "# " if kind == "OPTIONAL" else ""
        out.append(f"{prefix}export {member.name}={example}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _current_value_display(member: MetaprocEnv) -> str:
    """Return the current value for display; redact when SECRET."""
    raw = member.read_str(default=None)
    if raw is None:
        return ""
    if member.kind == "SECRET":
        return f"<set, len={len(raw)}>"
    return raw


def _normalize_kind(kind: str | None) -> EnvKind | None:
    """Validate and narrow a user-supplied --kind value."""
    if kind is None:
        return None
    normalized = kind.upper()
    if normalized not in get_args(EnvKind):
        valid = ", ".join(get_args(EnvKind))
        raise typer.BadParameter(f"{kind!r} — expected one of: {valid}", param_hint="--kind")
    return cast(EnvKind, normalized)


@app.command("env")
def env_cmd(
    kind: str | None = typer.Option(
        None, "--kind", help="Filter by kind: REAL, SECRET, TUNABLE, OPTIONAL."
    ),
    name_filter: str | None = typer.Option(
        None, "--name", help="Case-insensitive substring filter on env var name."
    ),
    only_set: bool = typer.Option(
        False, "--only-set", help="Show only vars that currently have a value."
    ),
    show_examples: bool = typer.Option(
        False, "--examples", help="Include registry example values in the output."
    ),
    template: bool = typer.Option(
        False,
        "--template",
        help="Print a regenerated `.env.example` to stdout. Pipe to `> .env.example`.",
    ),
) -> None:
    """List MetaprocEnv entries with kind, current value, and description.

    Default view shows every registered env var grouped by kind
    (REAL → SECRET → TUNABLE → OPTIONAL). Use ``--format json`` on the
    main CLI for a machine-readable form, or ``--template`` to regenerate
    ``.env.example`` directly from the registry.
    """
    out = get_output()
    if template:
        for line in render_env_template().splitlines():
            out.data(line)
        return
    requested_kind = _normalize_kind(kind)
    filter_lower = (name_filter or "").lower()

    members = sorted(MetaprocEnv, key=lambda m: (_KIND_ORDER[m.kind], m.name))
    rows: list[dict[str, str]] = []
    for m in members:
        if requested_kind is not None and m.kind != requested_kind:
            continue
        if filter_lower and filter_lower not in m.name.lower():
            continue
        value = _current_value_display(m)
        if only_set and not value:
            continue
        rows.append(
            {
                "name": m.name,
                "kind": m.kind,
                "value": value,
                "description": m.description,
                "example": m.example or "",
            }
        )

    if out.format == OutputFormat.JSON:
        out.data(rows)
        return

    if not rows:
        out.progress("No entries match the current filters.")
        return

    name_w = max(len(r["name"]) for r in rows)
    kind_w = max(len(r["kind"]) for r in rows)
    value_w = min(40, max(len("<unset>"), max(len(r["value"]) for r in rows) or 1))

    out.data(f"{'NAME':<{name_w}}  {'KIND':<{kind_w}}  {'VALUE':<{value_w}}  DESCRIPTION")
    out.data("-" * (name_w + kind_w + value_w + len("DESCRIPTION") + 6))
    for r in rows:
        value_display = r["value"] or "<unset>"
        if len(value_display) > value_w:
            value_display = value_display[: value_w - 1] + "…"
        out.data(
            f"{r['name']:<{name_w}}  {r['kind']:<{kind_w}}  "
            f"{value_display:<{value_w}}  {r['description']}"
        )
        if show_examples and r["example"]:
            out.data(f"{'':<{name_w}}  {'':<{kind_w}}  example: {r['example']}")

    set_count = sum(1 for r in rows if r["value"])
    out.progress(f"\n{len(rows)} entries shown ({set_count} set, {len(rows) - set_count} unset)")
