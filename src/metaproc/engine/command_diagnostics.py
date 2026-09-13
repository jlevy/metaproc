"""Bounded command diagnostics for durable task errors; raw capture stays in task logs."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

from metaproc.config.env_vars import MetaprocEnv
from metaproc.engine.retry import FailureClass, classify_failure

_SECRET_ENV_NAME = re.compile(
    r"(?:^|_)(?:TOKEN|KEY|SECRET|PASSWORD|PASSWD|CREDENTIALS?|CREDS|AUTH|BEARER)"
    r"(?:_(?:JSON|BASE64))?$",
    re.IGNORECASE,
)
_FRAMEWORK_ENV_KINDS = {variable.name: variable.kind for variable in MetaprocEnv}
_AUTH_VALUE = re.compile(r"(?i)\b(Bearer|Basic)\s+[^\s\"',;}]+")
_CREDENTIAL_VALUE = re.compile(
    r"""(?ix)(["']?(?:api[_-]?key|(?:access|refresh|id)[_-]?token|token|secret|password|client[_-]?secret)["']?\s*[:=]\s*)"""
    r"""(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;&}]+)"""
)
_URL_USERINFO = re.compile(r"(https?://)[^/\s@]+@", re.IGNORECASE)
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_MAX_DETAIL_CHARS = 1_200
_MAX_DETAIL_LINES = 12


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
    stderr = _ANSI_ESCAPE.sub("", stderr or "")
    stdout = _ANSI_ESCAPE.sub("", stdout or "")
    source = "stderr" if stderr.strip() else "stdout"
    detail = (stderr if source == "stderr" else stdout) or ""
    if not detail.strip():
        error = f"command exit code {returncode} (no stdout/stderr captured)"
        return CommandFailure(error=error, failure_class=classify_failure(error))

    try:
        secret_refs = json.loads(env.get(MetaprocEnv.METAPROC_GCP_SECRET_REFS_JSON.name, "{}"))
    except json.JSONDecodeError:
        secret_refs = {}
    declared_secrets = set(secret_refs) if isinstance(secret_refs, dict) else set()
    secrets = {
        _ANSI_ESCAPE.sub("", value)
        for name, value in env.items()
        if value
        and (
            name in declared_secrets
            or _FRAMEWORK_ENV_KINDS.get(name) == "SECRET"
            or (name not in _FRAMEWORK_ENV_KINDS and _SECRET_ENV_NAME.search(name) is not None)
        )
    }
    secrets.discard("")
    for value in sorted(secrets, key=len, reverse=True):
        detail = detail.replace(value, "[redacted]")
    detail = _AUTH_VALUE.sub(r"\1 [redacted]", detail)
    detail = _CREDENTIAL_VALUE.sub(r"\1[redacted]", detail)
    detail = _URL_USERINFO.sub(r"\1[redacted]@", detail)
    failure_class = classify_failure(f"command exit code {returncode} ({source}: {detail})")
    lines = [line.strip() for line in detail.splitlines() if line.strip()]
    truncated = len(lines) > _MAX_DETAIL_LINES
    detail = "\n".join(lines[-_MAX_DETAIL_LINES:])
    if len(detail) > _MAX_DETAIL_CHARS:
        detail = detail[-_MAX_DETAIL_CHARS:]
        truncated = True
    if truncated:
        detail = "[truncated]\n" + detail
    return CommandFailure(
        error=f"command exit code {returncode} ({source}: {detail}; log: {log_path})",
        failure_class=failure_class,
    )
