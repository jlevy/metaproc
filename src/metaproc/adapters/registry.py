"""Adapter registry — lookup adapters by type string."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import cast

from metaproc.adapters.base import Adapter, AuthCapableCliAdapter
from metaproc.adapters.claude_cli import ClaudeCodeCliAdapter
from metaproc.adapters.codex_cli import CodexCliAdapter
from metaproc.adapters.gemini_cli import GeminiCliAdapter
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


UNRECORDED_NAMESPACE_DEFAULT_MODELS: Mapping[str, str] = {"codex-cli": "gpt-5.5"}
"""Adapter defaults a run created before ``run-config.yaml`` recorded them derived against.

``namespace_default_models`` pins, for the life of a run, the adapter defaults its
derived artifact namespaces compare against (``derive_variant``). A ``run-config.yaml``
written before that field existed came from a release whose defaults were the current
ones except for the adapters listed here: codex-cli defaulted to ``gpt-5.5`` until the
2026-11-18 catalog review moved it to ``gpt-6.1-sol``.
"""


def current_default_models() -> dict[str, str]:
    """Return each registered adapter's current default model."""
    return {
        name: adapter.default_model
        for name, adapter in sorted(ADAPTER_REGISTRY.items())
        if adapter.default_model
    }


def recorded_namespace_default_models(recorded: object) -> dict[str, str]:
    """Return the adapter defaults a run's derived namespaces compare against.

    *recorded* is the ``namespace_default_models`` value of the run's
    ``run-config.yaml``, ``None`` when the config predates the field. The current
    defaults fill every adapter it does not name, and a config without the field takes
    ``UNRECORDED_NAMESPACE_DEFAULT_MODELS``, the defaults of the releases that wrote it.
    """
    defaults = current_default_models()
    if not isinstance(recorded, Mapping):
        return {**defaults, **UNRECORDED_NAMESPACE_DEFAULT_MODELS}
    for adapter_type, model in cast("Mapping[object, object]", recorded).items():
        if isinstance(adapter_type, str) and isinstance(model, str) and model:
            defaults[adapter_type] = model
    return defaults


def derive_variant(
    adapter_type: str,
    runtime_config: Mapping[str, object],
    *,
    default_models: Mapping[str, str] | None = None,
) -> str:
    """Derive a variant directory name from adapter type and config.

    A model equal to the adapter's default derives the bare adapter name; any other
    model is appended. *default_models* replaces the current default of each adapter it
    names, so a resume derives against the defaults its run was created with
    (``recorded_namespace_default_models``) and a default moved by a later release does
    not rename an existing run's namespace.
    """
    adapter = ADAPTER_REGISTRY.get(adapter_type)
    short = adapter.short_name if adapter else _sanitize_tag(adapter_type)
    model = runtime_config.get("model") or None
    default_model = (default_models or {}).get(adapter_type) or (
        adapter.default_model if adapter else None
    )

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
