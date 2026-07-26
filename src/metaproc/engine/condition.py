"""Predicate evaluator for ``ProcessOutput.condition``.

A tiny grammar: equality against a resolved variable, with the RHS optionally
quoted. Ported from the retired ``packet_manifest.form_is_active`` helper.
"""

from __future__ import annotations

import re

_EQUALITY_CONDITION = re.compile(
    r"""^\s*
    (?P<variable>[A-Za-z_][A-Za-z0-9_]*)
    \s*==\s*
    (?:
        '(?P<single_quoted>[^']*)'
        |"(?P<double_quoted>[^"]*)"
        |(?P<unquoted>[A-Za-z0-9_.:/-]+)
    )
    \s*$""",
    re.VERBOSE,
)


def output_is_active(
    condition: str | None,
    variables: dict[str, str] | None = None,
) -> bool:
    """Return whether a ``ProcessOutput`` applies for the current run variables.

    A ``None`` or empty ``condition`` is always active.
    """
    if not condition:
        return True
    if variables is None:
        return False

    match = _EQUALITY_CONDITION.fullmatch(condition)
    if match is None:
        msg = (
            f"unsupported ProcessOutput.condition: {condition!r} "
            "(expected NAME == value with an optional quoted value)"
        )
        raise NotImplementedError(msg)
    expected = next(
        value
        for value in (
            match.group("single_quoted"),
            match.group("double_quoted"),
            match.group("unquoted"),
        )
        if value is not None
    )
    return variables.get(match.group("variable")) == expected
