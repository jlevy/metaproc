"""Shared helpers for trace extractors."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from metaproc.logutil.usage import UsageStats, compute_cost
from metaproc.logutil.usage import load_pricing as _load
from metaproc.plugins.discovery import get_plugin_registry
from metaproc.trace.schema import bounded_attr_text, tool_usage_attrs

log = logging.getLogger(__name__)

# Per-attribute env/dict key tuples. Centralized so every extractor reads
# the same set of fallbacks for an attribute and additions land in one
# place. The canonical key is the snake_case one (matches the attribute
# the extractor writes onto the span); env-var aliases (METAPROC_LANE_ID,
# EXECUTION_PROFILE, ARTIFACT_NAMESPACE) are the env vars that
# ``run_parallel`` injects into the subprocess environment.

LANE_ID_KEYS: tuple[str, ...] = (
    "lane_id",
    "laneId",
    "LANE_ID",
    "METAPROC_LANE_ID",
)
"""Keys to look up an execution lane id in record/details/env mappings."""

EXECUTION_PROFILE_KEYS: tuple[str, ...] = (
    "execution_profile",
    "executionProfile",
    "EXECUTION_PROFILE",
)
"""Keys to look up an execution profile in record/details/env mappings."""

ARTIFACT_NAMESPACE_KEYS: tuple[str, ...] = (
    "artifact_namespace",
    "artifactNamespace",
    "ARTIFACT_NAMESPACE",
)
"""Keys to look up the artifact namespace in record/details/env mappings."""


def first_mapping_str(
    *containers: Mapping[str, Any],
    keys: tuple[str, ...],
) -> str | None:
    """Return the first non-empty string from explicit keys across mappings."""

    for key in keys:
        for container in containers:
            value = container.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def first_positional_arg(args: object) -> str | None:
    """Return the first positional CLI argument, skipping flag values."""

    if not isinstance(args, list):
        return None
    skip_next = False
    for arg in args:
        if not isinstance(arg, str):
            continue
        if skip_next:
            skip_next = False
            continue
        if arg.startswith("--"):
            if "=" not in arg:
                skip_next = True
            continue
        return arg
    return None


# ── Adapter-neutral tool-name → (family, operation, usage_role) classifier ──
#
# Moved here from claude_agent.py under P1.1. Every per-adapter
# extractor (claude, codex, gemini, pi) routes its raw tool name through this
# helper so the canonical attribute taxonomy (`tool.family`, `tool.operation`,
# `tool.usage_role`) is identical across adapters and roll-ups stay
# comparable.

# Gemini-cli emits snake_case names (`google_web_search`, `run_shell_command`,
# `read_file`, `write_file`, `replace`). Pi-cli emits camelCase
# (`webSearch`, `bash`, `read`, `write`). Claude uses PascalCase
# (`WebSearch`, `Bash`, `Read`, `Edit`). Normalize all to lower-snake for the
# classifier's dispatch.


def _normalize_tool_name(name: str) -> str:
    """Lowercased form for classifier dispatch.

    Claude's `WebSearch`, gemini's `google_web_search`, pi's `webSearch`,
    and `MultiEdit` all collapse to a stable lower-case form that the
    alias table dispatches on. We do NOT insert underscores so multi-word
    names like `MultiEdit` and `webSearch` map cleanly (`multiedit`,
    `websearch`) without colliding with snake-cased natives.
    """
    return name.lower()


# Tool-name aliases across adapters. Maps the adapter's raw spelling to
# the canonical name the classifier dispatches on.
_TOOL_NAME_ALIASES: dict[str, str] = {
    "google_web_search": "websearch",
    "web_search": "websearch",
    "websearch": "websearch",
    "web_fetch": "webfetch",
    "webfetch": "webfetch",
    "run_shell_command": "bash",
    "shell": "bash",
    "bash": "bash",
    "read_file": "read",
    "read": "read",
    "write_file": "write",
    "write": "write",
    "replace": "edit",  # gemini's in-place replace
    "edit": "edit",
    "multiedit": "multiedit",
    "notebookedit": "notebookedit",
    "grep": "grep",
    "glob": "glob",
}


def tool_usage_classification(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Return canonical tool-usage attributes for a single tool call.

    Shared across all per-adapter extractors. Keys: `tool.family`,
    `tool.operation`, `tool.usage_role`, plus tool-specific input fields
    (query/url/command/path). `source_origin` is set for events the
    classifier can confidently attribute.
    """
    normalized = _normalize_tool_name(tool_name)
    canonical = _TOOL_NAME_ALIASES.get(normalized, normalized)

    if canonical == "websearch":
        return tool_usage_attrs(
            tool__family="web",
            tool__operation="search",
            tool__usage_role="research",
            tool__input__query=bounded_attr_text(tool_input.get("query") or tool_input.get("q")),
            source_origin="agent_web_search",
        )
    if canonical == "webfetch":
        return tool_usage_attrs(
            tool__family="web",
            tool__operation="fetch",
            tool__usage_role="research",
            tool__input__url=bounded_attr_text(
                tool_input.get("url") or tool_input.get("uri"), max_chars=1_000
            ),
            tool__input__prompt_summary=bounded_attr_text(tool_input.get("prompt")),
            source_origin="agent_web_fetch",
        )
    if canonical == "bash":
        command = bounded_attr_text(
            tool_input.get("command") or tool_input.get("cmd"), max_chars=2_000
        )
        return bash_tool_usage_attrs(command)
    if canonical == "read":
        path = (
            tool_input.get("file_path") or tool_input.get("path") or tool_input.get("absolute_path")
        )
        return tool_usage_attrs(
            tool__family="file",
            tool__operation="read",
            tool__usage_role="artifact_io",
            tool__input__path=bounded_attr_text(path, max_chars=1_000),
            source_origin="local_input",
        )
    if canonical in {"write", "edit", "multiedit", "notebookedit"}:
        path = (
            tool_input.get("file_path") or tool_input.get("path") or tool_input.get("absolute_path")
        )
        return tool_usage_attrs(
            tool__family="file",
            tool__operation="write",
            tool__usage_role="artifact_io",
            tool__input__path=bounded_attr_text(path, max_chars=1_000),
        )
    if canonical in {"grep", "glob"}:
        return tool_usage_attrs(
            tool__family="file",
            tool__operation="search",
            tool__usage_role="artifact_io",
        )
    if canonical.startswith(("mcp__", "mcp_")):
        return tool_usage_attrs(
            tool__family="mcp",
            tool__operation="execute",
            tool__usage_role="api",
        )
    return {}


def bash_tool_usage_attrs(command: str | None) -> dict[str, Any]:
    """Classify a shell command and apply installed consumer classifiers."""
    if not command:
        return tool_usage_attrs(
            tool__family="shell",
            tool__operation="execute",
            tool__usage_role="orchestration",
        )

    attrs = tool_usage_attrs(
        tool__family="shell",
        tool__operation="execute",
        tool__usage_role="orchestration",
        tool__input__command=command,
    )
    invoked = None
    for classifier in get_plugin_registry().shell_command_classifiers:
        invoked = classifier(command)
        if invoked:
            break
    if not invoked:
        return attrs

    attrs.update(
        tool_usage_attrs(
            tool__family=str(invoked["family"]),
            tool__operation=str(invoked["operation"]),
            tool__usage_role=str(invoked["usage_role"]),
            tool__invoked__name=str(invoked["name"]),
            tool__input__ref=invoked.get("ref"),
            provider=str(invoked["provider"]),
            source_origin=str(invoked["source_origin"]),
        )
    )
    return attrs


# ── Attempt cost attributes (P1.6) ──


_pricing_cache: dict[str, dict[str, Any]] | None = None


def load_pricing() -> dict[str, dict[str, Any]]:
    """Re-export for patch-ability; delegates to ``metaproc.logutil.usage``."""

    return _load()


def _get_pricing_table() -> dict[str, dict[str, Any]]:
    """Lazy-load the pricing table (cached per process)."""
    global _pricing_cache  # noqa: PLW0603
    if _pricing_cache is None:
        _pricing_cache = load_pricing()
    return _pricing_cache


def attempt_cost_attrs(
    *,
    model: str | None,
    provider: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_tokens: int | None,
    self_reported_cost: float | None,
) -> dict[str, Any]:
    """Build canonical cost + token attributes for an ``attempt`` span.

    Three-tier rule:
    1. Self-reported cost (adapter tells us the cost) -> ``is_estimated=False``
    2. Pricing-table estimate (model found in ``pricing.md``) -> ``is_estimated=True``
    3. No cost available (unknown model, no self-report) -> cost keys omitted

    Token attributes are always included when non-None.
    """
    attrs: dict[str, Any] = {}

    # Token attributes
    if input_tokens is not None:
        attrs["attempt.tokens_input"] = input_tokens
    if output_tokens is not None:
        attrs["attempt.tokens_output"] = output_tokens
    if cached_tokens is not None:
        attrs["attempt.tokens_cached"] = cached_tokens

    # Cost: prefer self-reported
    if self_reported_cost is not None:
        attrs["attempt.cost_usd"] = self_reported_cost
        attrs["attempt.cost_is_estimated"] = False
        return attrs

    # Cost: pricing-table fallback
    if model and (input_tokens is not None or output_tokens is not None):
        pricing = _get_pricing_table()
        us = UsageStats(
            input_tokens=input_tokens or 0,
            output_tokens=output_tokens or 0,
            cache_read_tokens=cached_tokens or 0,
            model=model,
            provider=provider or "",
        )
        estimated_cost = compute_cost(us, pricing)
        if estimated_cost > 0:
            attrs["attempt.cost_usd"] = estimated_cost
            attrs["attempt.cost_is_estimated"] = True
        else:
            log.debug("No pricing entry for model %r; cost not computed", model)

    return attrs


# ── File-edit collapse rule (C1a in plan-2026-05-26-multi-adapter-resource-rollup) ──


def collapse_consecutive_file_edits(
    events: list[dict[str, Any]],
    *,
    path_of: Callable[[dict[str, Any]], str | None],
    is_file_edit: Callable[[dict[str, Any]], bool],
) -> list[dict[str, Any]]:
    """Collapse consecutive same-path file-edit events into one logical event.

    Each per-edit event (codex emits one per `file_change`) drowns the
    trace store and the reports for what is semantically a single agent
    action. This helper merges runs of consecutive same-path edits into a
    single dict carrying the merged metadata:

    - the first event's payload (kept as `merged_from`)
    - `edit_count` = number of raw events collapsed
    - `start_event` and `end_event` references for ts derivation

    Caller passes:
    - `path_of(event)` → str path or None if the event has no path
    - `is_file_edit(event)` → bool, true iff the event is a file edit

    Non-file-edit events pass through unchanged. Interleaving with a
    different path or with a non-file-edit event breaks the run.

    Returns: list of dicts shaped like::

        # passthrough (not a file edit):
        {"event": <original>}

        # collapsed file edit (count >= 1):
        {
            "event": <first event>,
            "edit_count": N,
            "path": <path>,
            "events": [<event>, ...],  # all merged events in order
        }
    """
    out: list[dict[str, Any]] = []
    i = 0
    n = len(events)
    while i < n:
        ev = events[i]
        if not is_file_edit(ev):
            out.append({"event": ev})
            i += 1
            continue
        path = path_of(ev)
        # Find run of consecutive events with same path that are also file edits.
        run: list[dict[str, Any]] = [ev]
        j = i + 1
        while j < n and is_file_edit(events[j]) and path_of(events[j]) == path:
            run.append(events[j])
            j += 1
        out.append(
            {
                "event": run[0],
                "edit_count": len(run),
                "path": path,
                "events": run,
            }
        )
        i = j
    return out
