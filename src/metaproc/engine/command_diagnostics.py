"""Bounded execution diagnostics; original command capture and tracebacks stay in task logs.

``metaproc.runtime.diagnostics.summarize_diagnostic`` is the public handler-facing wrapper
around the normalization, redaction, and clipping defined here.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from metaproc.config.env_vars import MetaprocEnv
from metaproc.engine.retry import FailureClass, RetryVerdict, classify_error, classify_failure

_SECRET_ENV_NAME = re.compile(
    r"(?:^|_)(?:TOKEN|KEY|SECRET|PASSWORD|PASSWD|CREDENTIALS?|CREDS|AUTH|BEARER)"
    r"(?:_(?:JSON|BASE64))?$",
    re.IGNORECASE,
)
_FRAMEWORK_ENV_KINDS = {variable.name: variable.kind for variable in MetaprocEnv}
# A name that merely looks secret is weak evidence. Values below a typical scanner floor,
# booleans, and numbers under such names are usually configuration, and replacing them
# would rewrite ordinary words and status codes that classification reads.
_MIN_HEURISTIC_SECRET_CHARS = 8
_NON_SECRET_LITERAL = re.compile(
    r"(?i)(?:true|false|yes|no|on|off|enabled|disabled|[+-]?\d+(?:\.\d+)?)"
)
_AUTH_VALUE = re.compile(r"(?i)\b(Bearer|Basic)\s+[^\s\"',;}]+")
_CREDENTIAL_VALUE = re.compile(
    r"""(?ix)(["']?(?:api[_-]?key|(?:access|refresh|id)[_-]?token|token|secret|password|client[_-]?secret)["']?\s*[:=]\s*)"""
    r"""(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;&}]+)"""
)
_URL_USERINFO = re.compile(r"(https?://)[^/\s@]+@", re.IGNORECASE)
_ANSI_ESCAPE = re.compile(
    r"(?:\x1b\]|\x9d)[^\x07\x1b\x9c]*(?:\x07|\x1b\\|\x9c)"
    r"|(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]"
)
_NONPRINTING_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x84\x86-\x9f]")
_JSON_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')
_MAX_DETAIL_CHARS = 1_200
_MAX_DETAIL_LINES = 12

# Command and handler messages end by naming their own attempt's retained evidence:
# each entry is the marker that introduces the path and the text that replaces the
# reference once the path is removed. This table is the grammar; the regex below,
# which finds the same references anywhere in a message rather than only at its end,
# is derived from it, so a third evidence form is added here alone.
_EVIDENCE_SUFFIXES: tuple[tuple[str, str], ...] = (("; log: ", ")"), (" (traceback: ", ""))


def _evidence_reference_pattern(marker: str, replacement: str) -> str:
    """One alternative matching *marker* and the path that runs to its closing paren.

    A reference the suffix rule replaces with ``)`` keeps that paren, so the pattern
    ends in a lookahead; one it replaces with nothing consumes the paren too.
    """
    closing = r"(?=\))" if replacement == ")" else r"\)"
    return re.escape(marker) + r"[^()\n]*?\.log" + closing


# The same references anywhere in a message, as nested process errors carry them.
_EVIDENCE_REFERENCES = re.compile(
    "|".join(
        _evidence_reference_pattern(marker, replacement)
        for marker, replacement in _EVIDENCE_SUFFIXES
    )
)
_MAX_SUMMARY_CAUSES = 5
_MAX_SUMMARY_CAUSE_CHARS = 1_500
_MAX_SUMMARY_CHARS = 4_000
_SUMMARY_OMISSION_RESERVE = 64


def normalize_diagnostic(text: str) -> str:
    """Remove terminal escape sequences and nonprinting controls from diagnostic text."""
    # OSC hyperlinks use either ST or BEL termination. Remove the complete control
    # sequence before redaction so hyperlink metadata cannot split a known secret.
    # Retain tabs and line breaks, but no controls that make durable YAML unreadable.
    return _NONPRINTING_CONTROL.sub("", _ANSI_ESCAPE.sub("", text))


def redact_diagnostic(text: str, *, env: Mapping[str, str]) -> str:
    """Return normalized text with known credentials redacted and nothing clipped.

    Classification reads this full redacted evidence; ``clip_diagnostic`` bounds it for
    display afterward.
    """
    try:
        secret_refs = json.loads(env.get(MetaprocEnv.METAPROC_GCP_SECRET_REFS_JSON.name, "{}"))
    except json.JSONDecodeError:
        secret_refs = {}
    declared_secrets = set(secret_refs) if isinstance(secret_refs, dict) else set()
    secrets = {
        normalize_diagnostic(value)
        for name, value in env.items()
        if value
        and (
            name in declared_secrets
            or _FRAMEWORK_ENV_KINDS.get(name) == "SECRET"
            or (
                name not in _FRAMEWORK_ENV_KINDS
                and _SECRET_ENV_NAME.search(name) is not None
                and _is_plausible_secret_value(value)
            )
        )
    }
    secrets.discard("")
    return _redact_text(text, secrets=sorted(secrets, key=len, reverse=True))


def _is_plausible_secret_value(value: str) -> bool:
    """Whether a value under a secret-like name is credential-shaped enough to redact.

    Declared secret references and framework ``SECRET`` variables bypass this check.
    """
    stripped = value.strip()
    return (
        len(stripped) >= _MIN_HEURISTIC_SECRET_CHARS
        and _NON_SECRET_LITERAL.fullmatch(stripped) is None
    )


def _redact_text(text: str, *, secrets: list[str]) -> str:
    def redact_encoded_string(match: re.Match[str]) -> str:
        encoded = match.group()
        if "\\" not in encoded:
            return encoded
        try:
            decoded: str = json.loads(encoded)
        except json.JSONDecodeError:
            return encoded
        # Each decoded string is shorter than its representation. Recurse so a JSON
        # response embedded in another JSON message receives the same checks. Keep
        # quoting/escaping intact rather than decoding arbitrary backslashes in paths.
        redacted = _redact_text(decoded, secrets=secrets)
        if redacted == decoded:
            return encoded
        # Preserve Unicode: newly spelling an ordinary character as a numeric escape
        # can introduce a status code into later classification. Escape lone surrogate
        # code points so the summary remains writable as UTF-8.
        return (
            json.dumps(redacted, ensure_ascii=False)
            .encode("utf-8", errors="backslashreplace")
            .decode("utf-8")
        )

    detail = _JSON_STRING.sub(redact_encoded_string, normalize_diagnostic(text))
    for value in secrets:
        detail = detail.replace(value, "[redacted]")
    detail = _AUTH_VALUE.sub(r"\1 [redacted]", detail)
    detail = _CREDENTIAL_VALUE.sub(r"\1[redacted]", detail)
    return _URL_USERINFO.sub(r"\1[redacted]@", detail)


def clip_diagnostic(detail: str) -> str:
    """Keep the last twelve nonempty lines and 1,200 characters, marking any omission."""
    lines = [line.strip() for line in detail.splitlines() if line.strip()]
    truncated = len(lines) > _MAX_DETAIL_LINES
    detail = "\n".join(lines[-_MAX_DETAIL_LINES:])
    if len(detail) > _MAX_DETAIL_CHARS:
        detail = detail[-_MAX_DETAIL_CHARS:]
        truncated = True
    return "[truncated]\n" + detail if truncated else detail


@dataclass(frozen=True)
class ExecutionFailure:
    """A durable failure message and the retry facts classified from its full evidence.

    ``error`` is clipped for display and carries an evidence path, so callers must use
    ``failure_class`` and ``verdict`` rather than classifying ``error`` again: a status
    code or permanent word in the path, or a line removed by clipping, would decide
    retry policy.
    """

    error: str
    failure_class: FailureClass
    verdict: RetryVerdict


def _classified(error: str, *, evidence: str) -> ExecutionFailure:
    return ExecutionFailure(
        error=error, failure_class=classify_failure(evidence), verdict=classify_error(evidence)
    )


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
    stderr = normalize_diagnostic(stderr or "")
    stdout = normalize_diagnostic(stdout or "")
    source = "stderr" if stderr.strip() else "stdout"
    detail = (stderr if source == "stderr" else stdout) or ""
    if not detail.strip():
        error = f"command exit code {returncode} (no stdout/stderr captured)"
        return _classified(error, evidence=error)

    detail = redact_diagnostic(detail, env=env)
    return _classified(
        f"command exit code {returncode} ({source}: {clip_diagnostic(detail)}; log: {log_path})",
        evidence=f"command exit code {returncode} ({source}: {detail})",
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
    detail = redact_diagnostic(str(exception), env=env)
    summary = clip_diagnostic(detail)
    message = f"{exception_type}: {summary}" if summary else exception_type
    return _classified(f"{message} (traceback: {log_path})", evidence=f"{exception_type}: {detail}")


def failure_cause(error: str) -> str:
    """Return a failure message without the evidence path that ends it.

    ``command_failure_message`` and ``handler_failure_message`` name the log of the
    attempt that failed, so identical failures of different items or attempts never
    compare equal as written. Only a final ``; log: <path>.log)`` or
    ``(traceback: <path>.log)`` is removed; any other message is returned unchanged.
    The records that hold the full message keep the path.
    """
    marker, replacement = max(_EVIDENCE_SUFFIXES, key=lambda suffix: error.rfind(suffix[0]))
    head, found, path = error.rpartition(marker)
    if not found or "\n" in path or not path.endswith(".log)"):
        return error
    return head + replacement


def without_evidence_paths(error: str) -> str:
    """Return a failure message with every attempt evidence reference removed.

    ``failure_cause`` removes the one path that ends a message. A composite's error
    nests the messages of the child steps that failed, each ending in the path of its
    own attempt's log, so a record that must read the same whenever the same failure
    recurs removes every ``(traceback: <path>.log)`` and every ``; log: <path>.log``
    inside ``(...)``. The message text stays, and the attempt log stays discoverable
    from the task's own state.
    """
    return _EVIDENCE_REFERENCES.sub("", error)


def summarize_failure_causes(errors: Iterable[str]) -> str:
    """Count failures by cause, most frequent first, in a bounded summary.

    Causes are compared after ``failure_cause`` removes evidence paths. The summary
    renders at most five causes, clips each to 1,500 characters, and stays within
    4,000 characters; the remainder is reported as the number of omitted causes and
    the failures they account for.
    """
    ranked = sorted(
        Counter(failure_cause(error) for error in errors).items(),
        key=lambda entry: (-entry[1], entry[0]),
    )
    parts: list[str] = []
    length = 0
    for cause, count in ranked[:_MAX_SUMMARY_CAUSES]:
        if len(cause) > _MAX_SUMMARY_CAUSE_CHARS:
            cause = f"{cause[:_MAX_SUMMARY_CAUSE_CHARS]} [truncated]"
        part = f"{count} x {cause}"
        if parts and length + len(part) > _MAX_SUMMARY_CHARS - _SUMMARY_OMISSION_RESERVE:
            break
        parts.append(part)
        length += len(part) + len("; ")
    omitted = ranked[len(parts) :]
    if omitted:
        omitted_failures = sum(count for _cause, count in omitted)
        parts.append(f"and {len(omitted)} more causes ({omitted_failures} items)")
    return "; ".join(parts)
