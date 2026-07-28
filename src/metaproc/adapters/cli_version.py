"""Shared pinned-CLI version verification for harness adapters.

Every CLI-backed adapter (claude, codex, gemini, pi) shells out to a harness
binary pinned to a specific version. Drift between the pin and the on-PATH
binary silently changes behavior — prompt handling, context compaction, the
tool surface — so each adapter declares a :class:`CliVersionSpec` and exposes
``preflight()``. ``run_process`` invokes ``preflight()`` for every distinct
adapter its active steps use before any step runs, turning drift into an
immediate, clear failure at launch instead of a mid-DAG surprise.
"""

from __future__ import annotations

import functools
import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)


class CliVersionMismatch(RuntimeError):
    """Raised when an on-PATH harness CLI does not match its pinned version."""


def first_token(output: str) -> str:
    """Default parser: the leading whitespace-delimited token (e.g. ``0.42.0``)."""
    return output.split()[0] if output else ""


def second_token_after(prefix: str) -> Callable[[str], str]:
    """Parser for ``<prefix> <version>`` output (e.g. ``codex-cli 0.135.0``)."""

    def _parse(output: str) -> str:
        parts = output.split()
        return parts[1] if len(parts) >= 2 and parts[0] == prefix else ""

    return _parse


@dataclass(frozen=True)
class CliVersionSpec:
    """Declares how to read and check one harness CLI's pinned version."""

    label: str
    cli_path: str
    expected: str
    version_args: tuple[str, ...] = ("--version",)
    parse: Callable[[str], str] = first_token
    exception: type[CliVersionMismatch] = CliVersionMismatch


def verify_cli_version(spec: CliVersionSpec) -> str:
    """Shell out to the pinned CLI, parse its version, and compare to the pin.

    Returns the parsed version on match. Raises ``spec.exception`` (a
    :class:`CliVersionMismatch` subclass) on drift, a missing binary, or a
    failing / unparsable ``--version``.
    """
    printed = f"{spec.cli_path} {' '.join(spec.version_args)}"
    try:
        proc = subprocess.run(  # noqa: S603 — cli_path is operator-controlled
            [spec.cli_path, *spec.version_args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise spec.exception(
            f"Could not run `{printed}`: {exc}. Expected pinned {spec.label} "
            f"version {spec.expected!r}; install it or fix PATH."
        ) from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise spec.exception(
            f"`{printed}` exited {proc.returncode}: {detail!r}. "
            f"Expected pinned {spec.label} version {spec.expected!r}."
        )
    output = proc.stdout.strip() or proc.stderr.strip()
    actual = spec.parse(output)
    if actual != spec.expected:
        raise spec.exception(
            f"{spec.label} CLI version mismatch: pinned={spec.expected!r}, "
            f"actual={actual!r} (`{printed}` -> {output!r}). Align the installed "
            "CLI to the pin, or update the pin and any deployment-image pins together."
        )
    return actual


@functools.cache
def _verify_cached(spec: CliVersionSpec) -> str:
    return verify_cli_version(spec)


def ensure_cli_version(spec: CliVersionSpec, *, skip: bool = False) -> str:
    """Verify the pin once per process (cached over the frozen *spec*), raising
    :class:`CliVersionMismatch` on drift.

    ``skip`` bypasses the check and returns the pinned value — for CI / test
    environments where the binary isn't installed. Callers read their own typed
    skip env var and pass the result so env access stays in the typed registry.
    """
    if skip:
        return spec.expected
    return _verify_cached(spec)


@functools.cache
def _drift_or_none(spec: CliVersionSpec) -> str | None:
    """Cached: return a drift message if the on-PATH CLI mismatches the pin,
    else None. Logs the drift once per process."""
    try:
        verify_cli_version(spec)
    except CliVersionMismatch as exc:
        log.warning("harness CLI version drift — %s", exc)
        return str(exc)
    return None


def check_cli_version(spec: CliVersionSpec, *, skip: bool = False) -> str | None:
    """Non-raising drift check. Returns a human-readable message on drift (for a
    caller to surface prominently), or None on match / skip.

    Unlike :func:`ensure_cli_version`, a version mismatch does **not** raise —
    version drift is surfaced as a prominent warning rather than a hard block, so
    an operator debugging odd behavior sees the likely cause without the run
    being stopped. The underlying shell-out is cached once per process.
    """
    if skip:
        return None
    return _drift_or_none(spec)
