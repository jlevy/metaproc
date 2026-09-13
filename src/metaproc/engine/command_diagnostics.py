"""Bounded command diagnostics for durable task errors; raw capture stays in task logs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from metaproc.engine.retry import FailureClass, classify_failure
from metaproc.runtime.diagnostics import (
    _clip_diagnostic,
    _normalize_diagnostic,
    _redact_diagnostic,
)


@dataclass(frozen=True)
class CommandFailure:
    error: str
    failure_class: FailureClass


def command_failure_message(
    returncode: int,
    *,
    stdout: str | None,
    stderr: str | None,
    env: Mapping[str, str],
    log_path: str,
) -> CommandFailure:
    """Return a bounded message and classify the credential-redacted diagnostic.

    Stderr owns command diagnostics when present; stdout is a fallback for commands
    that report errors there. Normalize terminal escapes before redaction, then classify
    before truncation or appending the log path: neither omitted evidence nor a filename
    containing a status code may change the category.
    """
    stderr = _normalize_diagnostic(stderr or "")
    stdout = _normalize_diagnostic(stdout or "")
    source = "stderr" if stderr.strip() else "stdout"
    detail = (stderr if source == "stderr" else stdout) or ""
    if not detail.strip():
        error = f"command exit code {returncode} (no stdout/stderr captured)"
        return CommandFailure(error=error, failure_class=classify_failure(error))

    detail = _redact_diagnostic(detail, env=env)
    failure_class = classify_failure(f"command exit code {returncode} ({source}: {detail})")
    detail = _clip_diagnostic(detail)
    return CommandFailure(
        error=f"command exit code {returncode} ({source}: {detail}; log: {log_path})",
        failure_class=failure_class,
    )
