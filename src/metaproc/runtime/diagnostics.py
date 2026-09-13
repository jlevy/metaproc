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
_ANSI_ESCAPE = re.compile(
    r"(?:\x1b\]|\x9d)[^\x07\x1b\x9c]*(?:\x07|\x1b\\|\x9c)"
    r"|(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]"
)
_NONPRINTING_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x84\x86-\x9f]")
_JSON_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')
_MAX_DETAIL_CHARS = 1_200
_MAX_DETAIL_LINES = 12


def summarize_diagnostic(text: str, *, env: Mapping[str, str] | None = None) -> str:
    """Return a bounded diagnostic with known credentials redacted.

    Remove terminal color/hyperlink sequences and nonprinting controls before matching
    environment secrets and common credential forms. JSON-encoded strings are checked
    as decoded values, including nested diagnostic payloads, before being re-encoded.
    Omitted ``env`` uses the current
    process environment; callers with resolved child credentials can provide that
    mapping explicitly. Redaction cannot identify
    arbitrary unlabeled secrets that are absent from this environment.

    The summary retains at most the last twelve nonempty lines and 1,200 characters,
    with an additional ``[truncated]`` marker when detail was omitted. It adds no exit
    status, exception type, classification, or evidence path. Callers own those facts
    and must retain original evidence separately when a summary is not sufficient.
    """
    return _clip_diagnostic(_redact_diagnostic(text, env=env if env is not None else os.environ))


def _normalize_diagnostic(text: str) -> str:
    # OSC hyperlinks use either ST or BEL termination. Remove the complete control
    # sequence before redaction so hyperlink metadata cannot split a known secret.
    # Retain tabs and line breaks, but no controls that make durable YAML unreadable.
    return _NONPRINTING_CONTROL.sub("", _ANSI_ESCAPE.sub("", text))


def _redact_diagnostic(text: str, *, env: Mapping[str, str]) -> str:
    """Keep full normalized evidence for classification before display clipping."""
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
    return _redact_text(text, secrets=sorted(secrets, key=len, reverse=True))


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

    detail = _JSON_STRING.sub(redact_encoded_string, _normalize_diagnostic(text))
    for value in secrets:
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
