"""Diagnostic summaries for persisted handler outputs and execution failures."""

from __future__ import annotations

import os
from collections.abc import Mapping

from metaproc.engine.command_diagnostics import clip_diagnostic, redact_diagnostic


def summarize_diagnostic(text: str, *, env: Mapping[str, str] | None = None) -> str:
    """Return a bounded diagnostic with known credentials redacted.

    Remove terminal color/hyperlink sequences and nonprinting controls before matching
    environment secrets and common credential forms. JSON-encoded strings are checked
    as decoded values, including nested diagnostic payloads, before being re-encoded.
    Omitted ``env`` uses the current
    process environment; callers with resolved child credentials can provide that
    mapping explicitly. Declared ``METAPROC_GCP_SECRET_REFS_JSON`` targets and framework
    ``SECRET`` variables are always redacted. Other names ending in a credential word such
    as ``TOKEN``, ``KEY``, or ``AUTH`` are redacted only when the value has at least eight
    characters and is not a boolean or number. Redaction cannot identify
    arbitrary unlabeled secrets that are absent from this environment.

    The summary retains at most the last twelve nonempty lines and 1,200 characters,
    with an additional ``[truncated]`` marker when detail was omitted. It adds no exit
    status, exception type, classification, or evidence path. Callers own those facts
    and must retain original evidence separately when a summary is not sufficient.
    """
    return clip_diagnostic(redact_diagnostic(text, env=env if env is not None else os.environ))
