"""Bounded execution diagnostics; original command capture and tracebacks stay in task logs."""

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
class ExecutionFailure:
    error: str
    failure_class: FailureClass


def command_failure_message(
    returncode: int,
    *,
    stdout: str | None,
    stderr: str | None,
    env: Mapping[str, str],
    log_path: str,
) -> ExecutionFailure:
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
        return ExecutionFailure(error=error, failure_class=classify_failure(error))

    detail = _redact_diagnostic(detail, env=env)
    failure_class = classify_failure(f"command exit code {returncode} ({source}: {detail})")
    detail = _clip_diagnostic(detail)
    return ExecutionFailure(
        error=f"command exit code {returncode} ({source}: {detail}; log: {log_path})",
        failure_class=failure_class,
    )


def handler_failure_message(
    exception: Exception,
    *,
    env: Mapping[str, str],
    log_path: str,
) -> ExecutionFailure:
    """Project an exception without treating it as a subprocess exit status.

    The caller retains the complete traceback at ``log_path``. Classify the full
    redacted exception before clipping its message or attaching that evidence path.
    """
    exception_type = type(exception).__name__
    detail = _redact_diagnostic(str(exception), env=env)
    summary = _clip_diagnostic(detail)
    message = f"{exception_type}: {summary}" if summary else exception_type
    return ExecutionFailure(
        error=f"{message} (traceback: {log_path})",
        failure_class=classify_failure(f"{exception_type}: {detail}"),
    )
