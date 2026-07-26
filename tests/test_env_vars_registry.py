"""Structural tests for :class:`metaproc.config.env_vars.MetaprocEnv`."""

from __future__ import annotations

from pathlib import Path

from metaproc.config.env_enum import EnvMeta
from metaproc.config.env_vars import SECRET_VARS, MetaprocEnv


def test_every_member_has_non_empty_description() -> None:
    missing = [m.name for m in MetaprocEnv if not m.description.strip()]
    assert not missing, f"Members without descriptions: {missing}"


def test_every_member_value_is_env_meta() -> None:
    """Guards against a stray string literal creeping back in as a member value."""
    for member in MetaprocEnv:
        assert isinstance(member.value, EnvMeta), (
            f"{member.name} has value {member.value!r}; must be EnvMeta"
        )


def test_every_member_has_valid_kind() -> None:
    valid = {"REAL", "TUNABLE", "SECRET", "OPTIONAL"}
    for member in MetaprocEnv:
        assert member.kind in valid, (
            f"{member.name} has invalid kind {member.kind!r}; must be one of {valid}"
        )


def test_secret_vars_subset_of_registry() -> None:
    for member in SECRET_VARS:
        assert isinstance(member, MetaprocEnv), f"{member!r} is not a MetaprocEnv member"


def test_registry_covers_env_example_vars() -> None:
    """Every `export FOO=...` entry in .env.example must be in the registry.

    Until Phase 3 regenerates .env.example from the registry, the two must
    agree on the intersection of "vars the operator sets".
    """
    env_example = Path(__file__).resolve().parents[1] / ".env.example"
    assert env_example.is_file(), f"missing {env_example}"

    documented: set[str] = set()
    for raw_line in env_example.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ")
        name, sep, _ = line.partition("=")
        if sep == "=":
            documented.add(name.strip())

    registry_names = {m.name for m in MetaprocEnv}
    missing_from_registry = documented - registry_names
    assert not missing_from_registry, (
        f"Vars in .env.example but missing from MetaprocEnv: {sorted(missing_from_registry)}"
    )
