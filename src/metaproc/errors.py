"""Structured error types with standard exit codes.

Exit codes:
  0 — success
  1 — general error
  2 — validation failure
  130 — interrupted (SIGINT)
"""

from __future__ import annotations

from typing import override


class CLIError(Exception):
    """Base error raised by CLI commands. Carries an exit code."""

    @override
    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code: int = exit_code


class ValidationError(CLIError):
    """Validation failure — exit code 2."""

    @override
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=2)
