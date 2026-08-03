"""Whole-attempt Claude token normalization.

One source of truth for "how many tokens did this Claude attempt use", shared
by the trace extractor and the resource-event extractor. They previously read
different fields off the same ``result`` event and therefore reported different
totals for the same file.

The distinction that matters: Anthropic documents the top-level ``usage`` block
as **excluding subagent activity**, while ``modelUsage`` covers the whole
attempt tree and preserves the per-model split. A run that delegated work is
undercounted by ``usage`` — sometimes by a lot — so ``modelUsage`` is preferred
whenever it carries at least one valid entry.

Neither field is a cost figure. ``result.total_cost_usd`` is a client-side
estimate Claude Code computes from a bundled price table; see
:mod:`metaproc.adapters.billing` for how that provenance is recorded.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ClaudeAttemptUsage:
    """Token totals for one Claude attempt, plus which models produced them."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    models: list[str] = field(default_factory=list)
    source: str = "none"
    """Which field the totals came from: ``modelUsage``, ``usage``, or ``none``."""

    @property
    def measured(self) -> bool:
        return self.source != "none"


def _aggregate_model_usage(model_usage: Mapping[str, Any]) -> ClaudeAttemptUsage | None:
    """Sum per-model entries, or ``None`` when no entry is usable.

    A nonempty ``modelUsage`` whose entries are all malformed must not shadow a
    perfectly good ``usage`` block, so "present but unusable" has to be
    distinguishable from "present and summed to zero".
    """
    totals = ClaudeAttemptUsage(source="modelUsage")
    aggregated = 0
    for model_name, usage in model_usage.items():
        if not isinstance(usage, dict):
            continue
        aggregated += 1
        totals.models.append(str(model_name))
        totals.input_tokens += int(usage.get("inputTokens") or 0)
        totals.output_tokens += int(usage.get("outputTokens") or 0)
        totals.cache_read_tokens += int(usage.get("cacheReadInputTokens") or 0)
        totals.cache_write_tokens += int(usage.get("cacheCreationInputTokens") or 0)
    if aggregated == 0:
        return None
    totals.models.sort()
    return totals


def claude_attempt_usage(result_event: Mapping[str, Any]) -> ClaudeAttemptUsage:
    """Whole-attempt token totals from a Claude ``result`` event.

    Prefers ``modelUsage`` (whole tree, including subagents), falling back to
    the top-level ``usage`` block when ``modelUsage`` is absent or carries no
    valid entries.
    """
    model_usage = result_event.get("modelUsage")
    if isinstance(model_usage, dict) and model_usage:
        aggregated = _aggregate_model_usage(model_usage)
        if aggregated is not None:
            return aggregated

    usage = result_event.get("usage")
    if isinstance(usage, dict):
        return ClaudeAttemptUsage(
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
            cache_write_tokens=int(usage.get("cache_creation_input_tokens") or 0),
            source="usage",
        )
    return ClaudeAttemptUsage()


__all__ = ["ClaudeAttemptUsage", "claude_attempt_usage"]
