"""Bounded command diagnostics for durable task errors; raw capture stays in task logs."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

from metaproc.config.env_vars import MetaprocEnv

_SECRET_ENV_MARKERS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIALS", "AUTH", "BEARER")
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


def command_failure_message(
    returncode: int,
    *,
    stdout: str | None,
    stderr: str | None,
    env: Mapping[str, str],
    log_path: str,
) -> str:
    """Keep exit status and the diagnostic stream's tail without copying credentials.

    Stderr owns command diagnostics when present; stdout is a fallback for commands
    that report errors there. Redact before truncating so cutting a long secret cannot
    leave an unrecognized prefix in durable state.
    """
    source = "stderr" if stderr and stderr.strip() else "stdout"
    detail = (stderr if source == "stderr" else stdout) or ""
    if not detail.strip():
        return f"command exit code {returncode} (no stdout/stderr captured)"

    try:
        secret_refs = json.loads(env.get(MetaprocEnv.METAPROC_GCP_SECRET_REFS_JSON.name, "{}"))
    except json.JSONDecodeError:
        secret_refs = {}
    declared_secrets = set(secret_refs) if isinstance(secret_refs, dict) else set()
    secrets = {
        value
        for name, value in env.items()
        if value
        and (
            name in declared_secrets
            or _FRAMEWORK_ENV_KINDS.get(name) == "SECRET"
            or (
                name not in _FRAMEWORK_ENV_KINDS
                and any(marker in name.upper() for marker in _SECRET_ENV_MARKERS)
            )
        )
    }
    for value in sorted(secrets, key=len, reverse=True):
        detail = detail.replace(value, "[redacted]")
    detail = _AUTH_VALUE.sub(r"\1 [redacted]", detail)
    detail = _CREDENTIAL_VALUE.sub(r"\1[redacted]", detail)
    detail = _URL_USERINFO.sub(r"\1[redacted]@", detail)
    detail = _ANSI_ESCAPE.sub("", detail)
    lines = [line.strip() for line in detail.splitlines() if line.strip()]
    truncated = len(lines) > _MAX_DETAIL_LINES
    detail = "\n".join(lines[-_MAX_DETAIL_LINES:])
    if len(detail) > _MAX_DETAIL_CHARS:
        detail = detail[-_MAX_DETAIL_CHARS:]
        truncated = True
    if truncated:
        detail = "[truncated]\n" + detail
    return f"command exit code {returncode} ({source}: {detail}; log: {log_path})"
