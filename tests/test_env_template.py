"""Golden test for ``metaproc env --template``.

The generated template MUST match the checked-in ``.env.example``. When this
test fails after a registry change, regenerate:

    uv --config-file uv.toml run --frozen metaproc env --template > .env.example

Coverage rationale: scattered ``.env.example`` edits drift from the registry
(at one point the template covered 18/88 vars). The CI gate here is what
keeps them in lock-step.
"""

from __future__ import annotations

from pathlib import Path

from metaproc.commands.env import render_env_template
from metaproc.config.env_vars import MetaprocEnv

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = REPO_ROOT / ".env.example"


def test_template_matches_checked_in_dot_env_example() -> None:
    expected = ENV_EXAMPLE.read_text()
    actual = render_env_template()
    assert actual == expected, (
        ".env.example is out of sync with the MetaprocEnv registry. "
        "Regenerate with: uv --config-file uv.toml run --frozen "
        "metaproc env --template > .env.example"
    )


def test_template_contains_every_non_optional_member() -> None:
    """REAL / SECRET / TUNABLE members must all appear; OPTIONAL is opt-in."""
    rendered = render_env_template()
    missing = [
        m.name
        for m in MetaprocEnv
        if m.kind in ("REAL", "SECRET", "TUNABLE") and m.name not in rendered
    ]
    assert not missing, f"Template missing required members: {missing}"


def test_template_skips_optional_without_example() -> None:
    """OPTIONAL members without an example produce noise, not signal — skip."""
    rendered = render_env_template()
    skipped = [m for m in MetaprocEnv if m.kind == "OPTIONAL" and not m.example]
    for member in skipped:
        assert f"export {member.name}" not in rendered, (
            f"OPTIONAL {member.name} has no example but appears in template"
        )


def test_template_comments_optional_members() -> None:
    """OPTIONAL members must be commented out so they stay unset by default."""
    rendered = render_env_template()
    for member in MetaprocEnv:
        if member.kind != "OPTIONAL" or not member.example:
            continue
        assert f"# export {member.name}=" in rendered, (
            f"OPTIONAL {member.name} should be commented out in template"
        )


def test_template_keeps_download_uris_with_required_digests() -> None:
    rendered = render_env_template()

    for name in (
        "METAPROC_WHEEL_GCS",
        "METAPROC_WHEEL_SHA256",
        "METAPROC_WORKSPACE_GCS",
        "METAPROC_WORKSPACE_SHA256",
    ):
        assert f"# export {name}=" in rendered
