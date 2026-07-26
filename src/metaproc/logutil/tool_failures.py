"""Tool-call failure classification.

Classifies a single ``tool_result`` block from an agent event stream into one
of eight failure kinds — plus ``adapter_dropped_call``, which is cross-event
(assigned by the aggregator when a ``tool_use`` has no matching
``tool_result``).

The classifier is heuristic over the ``error`` / ``content`` strings. It does
NOT attempt to retrofit fine-grained failure reasons onto shapes that do not
carry them; when no keyword matches, it falls back to the nearest generic
bucket (``tool_error``) rather than guessing.

Heuristics live here — not inline at the parse sites — so keyword drift in
provider error messages is a single-file change, and so adding new kinds
(``model_refusal``, ``tool_not_found``, etc.) has one obvious place to land.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class FailureKind(StrEnum):
    OK = "ok"
    MALFORMED_ARGS = "malformed_args"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_ERROR = "tool_error"
    HELP_INVOCATION = "help_invocation"
    TOOL_REJECTED = "tool_rejected"
    RATE_LIMIT_EXHAUSTED = "rate_limit_exhausted"
    ADAPTER_DROPPED_CALL = "adapter_dropped_call"
    UNKNOWN = "unknown"


TOOL_FAILURE_KINDS: tuple[FailureKind, ...] = (
    FailureKind.OK,
    FailureKind.MALFORMED_ARGS,
    FailureKind.TOOL_TIMEOUT,
    FailureKind.TOOL_ERROR,
    FailureKind.HELP_INVOCATION,
    FailureKind.TOOL_REJECTED,
    FailureKind.RATE_LIMIT_EXHAUSTED,
    FailureKind.ADAPTER_DROPPED_CALL,
    FailureKind.UNKNOWN,
)


_TIMEOUT_KEYWORDS = ("timed out", "timeout", "deadline exceeded")
_RATE_LIMIT_KEYWORDS = ("rate limit", "rate_limit", "429")
_MALFORMED_ARGS_KEYWORDS = ("invalid argument", "malformed", "invalid arg", "unrecognized argument")


def _classify_error_text(text: str) -> FailureKind:
    """Dispatch on substrings in a free-text error/content string.

    Checked in priority order: rate-limit wins over timeout (rate-limited calls
    often time out as a symptom), timeout wins over malformed args, malformed
    args win over generic tool_error.
    """
    lower = text.lower()
    if any(kw in lower for kw in _RATE_LIMIT_KEYWORDS):
        return FailureKind.RATE_LIMIT_EXHAUSTED
    if any(kw in lower for kw in _TIMEOUT_KEYWORDS):
        return FailureKind.TOOL_TIMEOUT
    if any(kw in lower for kw in _MALFORMED_ARGS_KEYWORDS):
        return FailureKind.MALFORMED_ARGS
    return FailureKind.TOOL_ERROR


def classify_pi_tool_result(block: dict[str, Any]) -> FailureKind:
    """Classify a single pi-cli (or Claude-Code) ``tool_result`` block.

    Expected shape:

        {"type": "tool_result", "is_error": bool, "content": str | list | object}

    Fails hard if ``type`` is not ``tool_result`` — the caller should filter to
    the right block type before dispatching.
    """
    if block.get("type") != "tool_result":
        raise ValueError(f"expected block.type == 'tool_result', got {block.get('type')!r}")

    is_error = bool(block.get("is_error", False))
    if not is_error:
        return FailureKind.OK

    content = block.get("content", "")
    text = content if isinstance(content, str) else str(content)
    return _classify_error_text(text)
