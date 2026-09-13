"""Diagnostic summaries for persisted handler outputs and execution failures."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping

from metaproc.config.env_vars import MetaprocEnv

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


def summarize_diagnostic(text: str, *, env: Mapping[str, str] | None = None) -> str:
    """Return a bounded diagnostic with known credentials redacted.

    Normalize ANSI escapes before matching environment secrets and common credential
    forms. Omitted ``env`` uses the current process environment; callers with resolved
    child credentials can provide that mapping explicitly. Redaction cannot identify
    arbitrary unlabeled secrets that are absent from this environment.

    The summary retains at most the last twelve nonempty lines and 1,200 characters,
    with an additional ``[truncated]`` marker when detail was omitted. It adds no exit
    status, exception type, classification, or evidence path. Callers own those facts
    and must retain original evidence separately when a summary is not sufficient.
    """
    return _clip_diagnostic(_redact_diagnostic(text, env=env if env is not None else os.environ))


def _normalize_diagnostic(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


def _redact_diagnostic(text: str, *, env: Mapping[str, str]) -> str:
    """Keep full normalized evidence for classification before display clipping."""
    detail = _normalize_diagnostic(text)
    try:
        secret_refs = json.loads(env.get(MetaprocEnv.METAPROC_GCP_SECRET_REFS_JSON.name, "{}"))
    except json.JSONDecodeError:
        secret_refs = {}
    declared_secrets = set(secret_refs) if isinstance(secret_refs, dict) else set()
    secrets = {
        _normalize_diagnostic(value)
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
    return _URL_USERINFO.sub(r"\1[redacted]@", detail)


def _clip_diagnostic(detail: str) -> str:
    lines = [line.strip() for line in detail.splitlines() if line.strip()]
    truncated = len(lines) > _MAX_DETAIL_LINES
    detail = "\n".join(lines[-_MAX_DETAIL_LINES:])
    if len(detail) > _MAX_DETAIL_CHARS:
        detail = detail[-_MAX_DETAIL_CHARS:]
        truncated = True
    return "[truncated]\n" + detail if truncated else detail
