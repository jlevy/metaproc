"""The gemini adapter's minimum-CLI gate.

The adapter passes ``--skip-trust``, a flag gemini-cli introduced in 0.40. Against an
older CLI every agent step fails mid-run with "Unknown arguments" and nothing points at
the cause, so the adapter refuses up front rather than warning. Drift at or above the
minimum stays a warning, because those runs can still succeed.
"""

from __future__ import annotations

import pytest

from metaproc.adapters import gemini_cli
from metaproc.adapters.gemini_cli import (
    MIN_GEMINI_CLI_VERSION,
    PINNED_GEMINI_CLI_VERSION,
    GeminiCliVersionMismatch,
    _below_minimum,
    _gemini_version_drift,
    _version_tuple,
)


def _drift_message(actual: str) -> str:
    """A drift string shaped like the one check_cli_version returns."""
    return (
        f"Gemini CLI version mismatch: expected={PINNED_GEMINI_CLI_VERSION!r} "
        f"actual={actual!r} "
        "(`gemini --version` -> '...'). Align the installed version."
    )


@pytest.fixture(autouse=True)
def _no_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize the bypass env var so each test drives the gate itself."""
    monkeypatch.delenv("METAPROC_SKIP_GEMINI_VERSION_CHECK", raising=False)


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("0.40.0", (0, 40, 0)),
        ("0.34.0", (0, 34, 0)),
        ("1.2", (1, 2)),
        ("0.41.0-nightly.1", (0, 41, 0, 1)),
        ("", ()),
        ("not-a-version", ()),
    ],
)
def test_version_tuple_parses_leading_numeric_components(
    version: str, expected: tuple[int, ...]
) -> None:
    assert _version_tuple(version) == expected


@pytest.mark.parametrize("version", ["0.34.0", "0.39.9", "0.0.1"])
def test_versions_below_the_floor_are_rejected(version: str) -> None:
    assert _below_minimum(version, MIN_GEMINI_CLI_VERSION)


@pytest.mark.parametrize(
    "version",
    [
        "0.40.0",
        "0.40.1",
        "0.41.0",
        "1.0.0",
        "0.41.0-nightly.1",
        # Zero-padded, so a CLI printing two components is not read as older than
        # the three-component floor. A bare tuple compare makes (0, 40) < (0, 40, 0).
        "0.40",
        "1.0",
    ],
)
def test_versions_at_or_above_the_floor_are_accepted(version: str) -> None:
    assert not _below_minimum(version, MIN_GEMINI_CLI_VERSION)


@pytest.mark.parametrize("version", ["", "junk", "vNext"])
def test_an_unreadable_version_is_never_below_the_floor(version: str) -> None:
    """This gate stops runs, so an unparsable version must not trip it."""
    assert not _below_minimum(version, MIN_GEMINI_CLI_VERSION)


def test_a_cli_below_the_minimum_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gemini_cli, "check_cli_version", lambda _spec: _drift_message("0.34.0"))
    with pytest.raises(GeminiCliVersionMismatch) as excinfo:
        _gemini_version_drift()
    message = str(excinfo.value)
    assert "0.34.0" in message
    assert MIN_GEMINI_CLI_VERSION in message
    assert "--skip-trust" in message


def test_a_cli_at_the_minimum_warns_but_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The boundary is inclusive: 0.40.0 has the flag, so the run can succeed."""
    drift = _drift_message(MIN_GEMINI_CLI_VERSION)
    monkeypatch.setattr(gemini_cli, "check_cli_version", lambda _spec: drift)
    assert _gemini_version_drift() == drift


def test_drift_above_the_minimum_is_returned_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drift = _drift_message("0.41.0")
    monkeypatch.setattr(gemini_cli, "check_cli_version", lambda _spec: drift)
    assert _gemini_version_drift() == drift


def test_no_drift_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gemini_cli, "check_cli_version", lambda _spec: None)
    assert _gemini_version_drift() is None


def test_an_unparsable_actual_version_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A drift string the regex cannot read must not be treated as below the floor.

    Refusing to run on an unreadable version would turn a cosmetic change in the CLI's
    output into an outage, so the gate degrades to the pre-existing warning.
    """
    drift = "Gemini CLI version mismatch: could not determine the installed version"
    monkeypatch.setattr(gemini_cli, "check_cli_version", lambda _spec: drift)
    assert _gemini_version_drift() == drift


@pytest.mark.parametrize("value", ["1", "true", "yes", "TRUE", "Yes"])
def test_the_skip_env_var_bypasses_the_gate_entirely(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Documented escape hatch: it must bypass the raise, not only the warning."""
    monkeypatch.setenv("METAPROC_SKIP_GEMINI_VERSION_CHECK", value)

    def _unreachable(_spec: object) -> str:
        raise AssertionError("the version check ran despite the skip flag")

    monkeypatch.setattr(gemini_cli, "check_cli_version", _unreachable)
    assert _gemini_version_drift() is None
