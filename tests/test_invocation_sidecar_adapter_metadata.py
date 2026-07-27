"""P0.2 — invocation sidecar adapter metadata.

Pins the contract that:
- `metaproc.adapters.registry.adapter_provider()` and `.adapter_trace_source()`
  return canonical labels for all four current adapter types.
- `claude_agent._source_from_invocation()` prefers the new sidecar key
  `adapter_trace_source` over the legacy `argv[0]` inference.

This is a pure unit test on the helper functions — does NOT run runpool
or launch any subprocesses.
"""

from __future__ import annotations

import pytest

from metaproc.adapters.registry import (
    ADAPTER_METADATA,
    ADAPTER_REGISTRY,
    adapter_provider,
    adapter_trace_source,
)
from metaproc.trace.extractors.claude_agent import _source_from_invocation


def test_adapter_metadata_covers_every_registered_adapter() -> None:
    """ADAPTER_METADATA must be kept in lockstep with ADAPTER_REGISTRY."""
    assert set(ADAPTER_METADATA.keys()) == set(ADAPTER_REGISTRY.keys())


@pytest.mark.parametrize(
    ("adapter_type", "expected_provider"),
    [
        ("claude-code-cli", "anthropic"),
        ("codex-cli", "openai"),
        ("gemini-cli", "google"),
        ("pi-cli", "pi"),
    ],
)
def test_adapter_provider_canonical(adapter_type: str, expected_provider: str) -> None:
    assert adapter_provider(adapter_type) == expected_provider


@pytest.mark.parametrize(
    ("adapter_type", "expected_source"),
    [
        ("claude-code-cli", "claude-agent"),
        ("codex-cli", "codex-agent"),
        ("gemini-cli", "gemini-agent"),
        ("pi-cli", "pi-agent"),
    ],
)
def test_adapter_trace_source_canonical(adapter_type: str, expected_source: str) -> None:
    assert adapter_trace_source(adapter_type) == expected_source


def test_adapter_provider_unknown_falls_through() -> None:
    """Unknown adapter types degrade gracefully (returns adapter_type itself)."""
    assert adapter_provider("future-adapter") == "future-adapter"


def test_source_from_invocation_prefers_explicit_trace_source() -> None:
    """When P0.2 metadata is present, use it; argv inference is the fallback."""
    invocation = {
        "argv": ["claude", "--print"],  # would resolve to claude-agent on its own
        "metadata": {
            "adapter_type": "codex-cli",
            "adapter_trace_source": "codex-agent",
        },
    }
    assert _source_from_invocation(invocation, default="X") == "codex-agent"


def test_source_from_invocation_falls_back_to_argv() -> None:
    """Sidecars from before P0.2 land use argv-based inference."""
    invocation = {"argv": ["codex", "--print"], "metadata": {}}
    assert _source_from_invocation(invocation, default="X") == "codex-agent"


def test_source_from_invocation_falls_back_to_default() -> None:
    """No usable signal at all → caller's default."""
    invocation = {"argv": [], "metadata": {}}
    assert _source_from_invocation(invocation, default="claude-agent") == "claude-agent"
