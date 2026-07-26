"""Adapter registry — lookup adapters by type string."""

from __future__ import annotations

import re
from typing import cast

from metaproc.adapters.base import Adapter, AuthCapableCliAdapter
from metaproc.adapters.claude_code import ClaudeCodeCliAdapter
from metaproc.adapters.codex import CodexCliAdapter
from metaproc.adapters.gemini import GeminiCliAdapter
from metaproc.adapters.pi_cli import PiCliAdapter

ADAPTER_REGISTRY = cast(
    "dict[str, Adapter]",
    {
        "claude-code-cli": ClaudeCodeCliAdapter(),
        "codex-cli": CodexCliAdapter(),
        "gemini-cli": GeminiCliAdapter(),
        "pi-cli": PiCliAdapter(),
    },
)

# Canonical (provider, agent-source-label) for each adapter type.
# Used by sidecar metadata and trace extractors. Keep in lockstep with
# ADAPTER_REGISTRY keys.
ADAPTER_METADATA: dict[str, dict[str, str]] = {
    "claude-code-cli": {"provider": "anthropic", "trace_source": "claude-agent"},
    "codex-cli": {"provider": "openai", "trace_source": "codex-agent"},
    "gemini-cli": {"provider": "google", "trace_source": "gemini-agent"},
    "pi-cli": {"provider": "pi", "trace_source": "pi-agent"},
}


def adapter_provider(adapter_type: str) -> str:
    """Canonical provider name for an adapter type.

    Falls back to the adapter_type itself if not in ADAPTER_METADATA so
    new adapters degrade gracefully until registered.
    """
    meta = ADAPTER_METADATA.get(adapter_type)
    return meta["provider"] if meta else adapter_type


def adapter_trace_source(adapter_type: str) -> str:
    """Canonical trace `source` label for an adapter type.

    Matches the value extractors stamp onto every emitted span, so
    `metaproc trace --filter source=codex-agent` works consistently
    across adapters.
    """
    meta = ADAPTER_METADATA.get(adapter_type)
    return meta["trace_source"] if meta else adapter_type


def _sanitize_tag(value: str) -> str:
    """Sanitize a string for safe use in filesystem paths."""
    sanitized = re.sub(r"[^a-zA-Z0-9._-]", "-", value)
    return re.sub(r"-{2,}", "-", sanitized).strip("-")


def derive_variant(adapter_type: str, runtime_config: dict[str, object]) -> str:
    """Derive a variant directory name from adapter type and config."""
    adapter = ADAPTER_REGISTRY.get(adapter_type)
    short = adapter.short_name if adapter else _sanitize_tag(adapter_type)
    model = runtime_config.get("model") or None
    default_model = adapter.default_model if adapter else None

    if model is None or model == default_model:
        return short

    return _sanitize_tag(f"{short}-{model}")


def get_adapter(adapter_type: str) -> Adapter:
    """Look up an adapter by type string."""
    adapter = ADAPTER_REGISTRY.get(adapter_type)
    if adapter is None:
        supported = ", ".join(sorted(ADAPTER_REGISTRY))
        msg = f"unknown adapter type: {adapter_type!r} (supported: {supported})"
        raise ValueError(msg)
    return adapter


def get_auth_capable(adapter_type: str) -> AuthCapableCliAdapter | None:
    """Return the adapter only if it implements ``AuthCapableCliAdapter``.

    Used by the slot coordinator and ``metaproc auth`` subcommands
    before any slot-acquisition / quota / fallback work. Non-auth-capable
    adapters (e.g. Pi via ``ANTHROPIC_API_KEY``) become clean no-ops for
    the pool paths rather than runtime ``AttributeError``s.

    Returns ``None`` when the adapter is unregistered or when it does
    not implement the subprotocol. Callers must not fall back to
    ``hasattr`` probing.
    """
    adapter = ADAPTER_REGISTRY.get(adapter_type)
    if adapter is None:
        return None
    if isinstance(adapter, AuthCapableCliAdapter):
        return adapter
    return None
