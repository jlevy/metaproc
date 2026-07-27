"""Unit tests for :mod:`metaproc.config.env_enum`."""

from __future__ import annotations

from pathlib import Path

import pytest

from metaproc.config.env_enum import (
    REQUIRED,
    EnvEnum,
    InvalidEnvVar,
    MissingEnvVar,
    optional,
)


class _TestEnv(EnvEnum):
    X_STR = optional("String-valued test env var.")
    X_PATH = optional("Path-valued test env var.")
    X_BOOL = optional("Bool-valued test env var.")
    X_INT = optional("Int-valued test env var.")


# ── read_str ──────────────────────────────────────────────────────


def test_read_str_returns_set_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_STR", "hello")
    assert _TestEnv.X_STR.read_str() == "hello"


def test_read_str_required_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("X_STR", raising=False)
    with pytest.raises(MissingEnvVar) as excinfo:
        _TestEnv.X_STR.read_str()
    assert "X_STR" in str(excinfo.value)


def test_read_str_returns_default_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("X_STR", raising=False)
    assert _TestEnv.X_STR.read_str(default="fallback") == "fallback"


def test_read_str_none_default_allows_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("X_STR", raising=False)
    assert _TestEnv.X_STR.read_str(default=None) is None


def test_read_str_empty_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_STR", "")
    with pytest.raises(MissingEnvVar):
        _TestEnv.X_STR.read_str()


def test_read_str_whitespace_only_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_STR", "   ")
    assert _TestEnv.X_STR.read_str(default="default") == "default"


def test_read_str_changeme_placeholder_counts_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("X_STR", "changeme")
    with pytest.raises(MissingEnvVar):
        _TestEnv.X_STR.read_str()


def test_read_str_changeme_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_STR", "CHANGEME")
    assert _TestEnv.X_STR.read_str(default=None) is None


# ── read_int ──────────────────────────────────────────────────────


def test_read_int_returns_parsed_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_INT", "42")
    assert _TestEnv.X_INT.read_int() == 42


def test_read_int_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_INT", "  17  ")
    assert _TestEnv.X_INT.read_int() == 17


def test_read_int_raises_invalid_on_non_integer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_INT", "not-a-number")
    with pytest.raises(InvalidEnvVar) as excinfo:
        _TestEnv.X_INT.read_int()
    message = str(excinfo.value)
    assert "X_INT" in message
    assert "not-a-number" in message


def test_read_int_required_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("X_INT", raising=False)
    with pytest.raises(MissingEnvVar):
        _TestEnv.X_INT.read_int()


def test_read_int_returns_default_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("X_INT", raising=False)
    assert _TestEnv.X_INT.read_int(default=99) == 99


def test_read_int_none_default_allows_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("X_INT", raising=False)
    assert _TestEnv.X_INT.read_int(default=None) is None


def test_read_int_placeholder_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_INT", "changeme")
    assert _TestEnv.X_INT.read_int(default=7) == 7


# ── read_bool ─────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", ["1", "true", "True", "yes", "YES", "on", "anything"])
def test_read_bool_truthy_tokens(raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_BOOL", raw)
    assert _TestEnv.X_BOOL.read_bool() is True


@pytest.mark.parametrize("raw", ["0", "false", "FALSE", "no", "NO", "off", "OFF"])
def test_read_bool_falsy_tokens(raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_BOOL", raw)
    assert _TestEnv.X_BOOL.read_bool() is False


def test_read_bool_required_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("X_BOOL", raising=False)
    with pytest.raises(MissingEnvVar):
        _TestEnv.X_BOOL.read_bool()


def test_read_bool_default_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("X_BOOL", raising=False)
    assert _TestEnv.X_BOOL.read_bool(default=True) is True
    assert _TestEnv.X_BOOL.read_bool(default=False) is False


# ── read_path ─────────────────────────────────────────────────────


def test_read_path_returns_resolved_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("X_PATH", str(tmp_path))
    result = _TestEnv.X_PATH.read_path()
    assert isinstance(result, Path)
    assert result == tmp_path.resolve()


def test_read_path_expands_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_PATH", "~/some-path")
    result = _TestEnv.X_PATH.read_path()
    assert "~" not in str(result)


def test_read_path_required_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("X_PATH", raising=False)
    with pytest.raises(MissingEnvVar):
        _TestEnv.X_PATH.read_path()


def test_read_path_default_when_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("X_PATH", raising=False)
    assert _TestEnv.X_PATH.read_path(default=tmp_path) == tmp_path.resolve()


def test_read_path_none_default_allows_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("X_PATH", raising=False)
    assert _TestEnv.X_PATH.read_path(default=None) is None


def test_read_path_placeholder_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_PATH", "changeme")
    assert _TestEnv.X_PATH.read_path(default=None) is None


# ── REQUIRED sentinel ────────────────────────────────────────────


def test_required_repr() -> None:
    assert repr(REQUIRED) == "REQUIRED"


def test_enum_member_description_accessible() -> None:
    """The ``description`` property reads from the member's ``EnvMeta`` value."""
    for member in _TestEnv:
        assert member.description
        assert member.kind == "OPTIONAL"


def test_enum_member_name_is_env_var_name() -> None:
    """Accessors use ``self.name`` as the env var name — verify it matches the identifier."""
    assert _TestEnv.X_STR.name == "X_STR"
    assert _TestEnv.X_INT.name == "X_INT"
