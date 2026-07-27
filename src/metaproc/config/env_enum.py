"""Typed environment-variable registries.

Subclass :class:`EnvEnum` and declare one member per env var; each member's
value is an :class:`EnvMeta` constructed via the :func:`real`,
:func:`tunable`, :func:`secret`, or :func:`optional` factory. Read the
value later with a typed accessor that either returns a value, raises
:class:`MissingEnvVar`, or raises :class:`InvalidEnvVar`.

Example::

    from metaproc.config.env_enum import EnvEnum, real, secret, tunable

    class MyEnv(EnvEnum):
        MY_PROJECT = real("GCP project ID.", "my-project")
        MY_API_KEY = secret("API key for the backend service.")
        MY_TIMEOUT_S = tunable("Per-request timeout (seconds).", "30")

    project = MyEnv.MY_PROJECT.read_str()             # required
    timeout = MyEnv.MY_TIMEOUT_S.read_int(default=30)

The env var NAME is the member's Python identifier (``self.name``); the
structural metadata (description, kind, example) lives on ``self.value``
and is exposed through the :attr:`~EnvEnum.description`, :attr:`~EnvEnum.kind`,
and :attr:`~EnvEnum.example` properties.

See ``docs/guidelines/python-structural-quality-guidelines.md`` (section
"Environment Variables: Typed Registry, Not Scattered Reads") for the
full rationale and repeat-across-projects guideline.

Deviations from the kash/clideps reference:

- Member values carry structural metadata (kind, example, description)
  rather than being bare strings. Enables ``metaproc env`` template
  generation without a sidecar dict.
- Adds :meth:`EnvEnum.read_int` with :class:`InvalidEnvVar` on non-integer
  input.
- Empty strings and the placeholder ``"changeme"`` count as *unset* for
  all accessors — prevents ``.env.example`` placeholders from leaking into
  the runtime.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal, cast, overload, override

_PLACEHOLDER_VALUES: frozenset[str] = frozenset({"changeme"})


EnvKind = Literal["REAL", "TUNABLE", "SECRET", "OPTIONAL"]
"""Documentation legend for env vars in a registry.

- ``REAL``: real project value — the operator copies the example as-is.
- ``TUNABLE``: sensible default the operator may tune (quotas, paths, etc).
- ``SECRET``: illustration-only; must be replaced with a real secret.
- ``OPTIONAL``: omit from ``.env.example`` unless setting is deliberate;
  may be unset.
"""


@dataclass(frozen=True, slots=True, eq=False)
class EnvMeta:
    """Structural metadata for a single env var.

    ``eq=False`` keeps each instance distinct by identity, so that two
    members declared with the same factory args don't collapse into an
    enum alias.
    """

    description: str
    kind: EnvKind
    example: str | None = None


def real(description: str, example: str) -> EnvMeta:
    """Real project value — copy the example as-is into ``.env``."""
    return EnvMeta(description, "REAL", example)


def tunable(description: str, example: str) -> EnvMeta:
    """Sensible default the operator may tune for their situation."""
    return EnvMeta(description, "TUNABLE", example)


def secret(description: str, example: str | None = None) -> EnvMeta:
    """Illustration-only — must be replaced with a real secret before use."""
    return EnvMeta(description, "SECRET", example)


def optional(description: str, example: str | None = None) -> EnvMeta:
    """Optional variable; emitted commented-out in ``.env.example``."""
    return EnvMeta(description, "OPTIONAL", example)


class _RequiredType:
    @override
    def __repr__(self) -> str:
        return "REQUIRED"


REQUIRED = _RequiredType()
"""Sentinel for required env vars — reads without a default raise on miss."""


class MissingEnvVar(ValueError):
    """Raised when a required env var is unset (or a placeholder)."""

    def __init__(self, env_var: str) -> None:
        super().__init__(f"Required environment variable is not set: {env_var}")


class InvalidEnvVar(ValueError):
    """Raised when an env var is set but cannot be coerced to the expected type."""

    def __init__(self, env_var: str, value: str, expected: str) -> None:
        super().__init__(f"Environment variable {env_var}={value!r} is not a valid {expected}")


def _raw_value(name: str) -> str | None:
    """Return the raw env var value, treating empty / placeholder as unset."""
    value = os.environ.get(name)
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if stripped.lower() in _PLACEHOLDER_VALUES:
        return None
    return value


class EnvEnum(Enum):
    """Base class for typed env-var registries.

    Each member's value is an :class:`EnvMeta`; the member's Python
    identifier is the env var name. See module docstring for the full
    usage pattern.
    """

    @property
    def _meta(self) -> EnvMeta:
        return cast(EnvMeta, self.value)

    @property
    def description(self) -> str:
        """Operator-facing one-line description."""
        return self._meta.description

    @property
    def kind(self) -> EnvKind:
        """Legend category: REAL / TUNABLE / SECRET / OPTIONAL."""
        return self._meta.kind

    @property
    def example(self) -> str | None:
        """Illustration value shown in ``.env.example`` (``None`` = no default)."""
        return self._meta.example

    # ── read accessors ────────────────────────────────────────────────

    @overload
    def read_str(self) -> str: ...

    @overload
    def read_str(self, *, default: str) -> str: ...

    @overload
    def read_str(self, *, default: None) -> str | None: ...

    def read_str(self, *, default: str | None | _RequiredType = REQUIRED) -> str | None:
        """Return the string value; raise :class:`MissingEnvVar` if unset and required."""
        value = _raw_value(self.name)
        if value is not None:
            return value
        if isinstance(default, _RequiredType):
            raise MissingEnvVar(self.name)
        return default

    @overload
    def read_path(self) -> Path: ...

    @overload
    def read_path(self, *, default: Path) -> Path: ...

    @overload
    def read_path(self, *, default: None) -> Path | None: ...

    def read_path(self, *, default: Path | None | _RequiredType = REQUIRED) -> Path | None:
        """Return the value as a resolved :class:`~pathlib.Path`."""
        value = _raw_value(self.name)
        if value is not None:
            return Path(value).expanduser().resolve()
        if isinstance(default, _RequiredType):
            raise MissingEnvVar(self.name)
        if default is None:
            return None
        return default.expanduser().resolve()

    @overload
    def read_bool(self) -> bool: ...

    @overload
    def read_bool(self, *, default: bool) -> bool: ...

    def read_bool(self, *, default: bool | _RequiredType = REQUIRED) -> bool:
        """Return the value as a bool.

        Falsy tokens: empty, ``0``, ``false``, ``no``, ``off`` (case-insensitive).
        Everything else is truthy.
        """
        value = _raw_value(self.name)
        if value is not None:
            token = value.strip().lower()
            return bool(
                token and token != "0" and token != "false" and token != "no" and token != "off"
            )
        if isinstance(default, _RequiredType):
            raise MissingEnvVar(self.name)
        return default

    @overload
    def read_int(self) -> int: ...

    @overload
    def read_int(self, *, default: int) -> int: ...

    @overload
    def read_int(self, *, default: None) -> int | None: ...

    def read_int(self, *, default: int | None | _RequiredType = REQUIRED) -> int | None:
        """Return the value as an int; raise :class:`InvalidEnvVar` on non-numeric input."""
        value = _raw_value(self.name)
        if value is not None:
            try:
                return int(value.strip())
            except ValueError as exc:
                raise InvalidEnvVar(self.name, value, "integer") from exc
        if isinstance(default, _RequiredType):
            raise MissingEnvVar(self.name)
        return default
