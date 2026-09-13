"""Structured error types with standard exit codes.

Exit codes:
  0 — success
  1 — general error
  2 — validation failure
  130 — interrupted (SIGINT)
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    from metaproc.models.runtime import OutputFailure


class CLIError(Exception):
    """Base error raised by CLI commands. Carries an exit code."""

    @override
    def __init__(
        self,
        message: str,
        exit_code: int = 1,
        *,
        output_failures: Sequence[OutputFailure] = (),
    ) -> None:
        super().__init__(message)
        self.exit_code: int = exit_code
        self.output_failures: tuple[OutputFailure, ...] = tuple(output_failures)


class ValidationError(CLIError):
    """Validation failure — exit code 2."""

    @override
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=2)


class AttemptTerminalConflictError(ValueError):
    """A caller tried to replace an attempt's already-persisted terminal fact."""
