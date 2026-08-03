"""Built-in provider meters proven by terminal agent log records.

This module deliberately exposes evidence, not a provider integration. It never
calls a provider and never treats an agent turn, step, or tool span as an API
request. Unknown request boundaries are represented explicitly as unmeasured.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from metaproc.logutil.parsing import LogEvent
from metaproc.models.resources import (
    CoverageState,
    MeteredQuantity,
    MeterKey,
    ProviderRef,
)

_PROVIDER_BY_ADAPTER = {
    "claude": "anthropic",
    "codex": "openai",
    "gemini": "google",
}
_PRODUCT_BY_ADAPTER = {
    "claude": "claude-code",
    "codex": "codex-cli",
    "gemini": "gemini-cli",
    "pi": "pi-cli",
}
_TERMINAL_TYPES = {
    "claude": {"result"},
    "codex": {"turn.completed", "turn.failed"},
    "gemini": {"result"},
    "pi": {"agent_end"},
}


@dataclass(frozen=True)
class AgentProviderEvidence:
    """Credential-free provider identity and exact-key meter observations."""

    provider: ProviderRef
    meters: tuple[MeteredQuantity, ...]


def extract_agent_provider_evidence(
    *,
    adapter: str,
    model: str | None,
    events: list[LogEvent],
    provider: str | None = None,
) -> AgentProviderEvidence | None:
    """Return terminal provider evidence for one parsed source log.

    The adapter terminal boundary proves an agent invocation. Claude and
    Codex expose exact turn facts; Gemini and Pi do not expose a compatible
    exact turn count. No built-in adapter proves a provider API-request
    boundary, so that meter is always present as unmeasured.
    """
    product = _PRODUCT_BY_ADAPTER.get(adapter)
    if product is None:
        return None
    resolved_provider = (
        provider or _PROVIDER_BY_ADAPTER.get(adapter) or ("unknown" if adapter == "pi" else None)
    )
    if not resolved_provider:
        return None

    terminal_types = _TERMINAL_TYPES[adapter]
    terminals = [
        event.raw
        for event in events
        if isinstance(event.raw, dict) and event.raw.get("type") in terminal_types
    ]
    if not terminals:
        return None

    meters = [
        _measured(
            provider=resolved_provider,
            product=product,
            meter="agent_invocations",
            quantity=1,
            lineage="terminal boundary in one agent log",
        ),
        _turn_meter(
            adapter=adapter,
            provider=resolved_provider,
            product=product,
            terminals=terminals,
            events=events,
        ),
        _unmeasured(
            provider=resolved_provider,
            product=product,
            meter="api_requests",
            lineage="provider request boundary absent from agent log",
        ),
    ]
    if adapter == "claude":
        meters.extend(
            _claude_server_tool_meters(
                provider=resolved_provider,
                product=product,
                terminals=terminals,
                events=events,
            )
        )
    return AgentProviderEvidence(
        provider=ProviderRef(
            provider=resolved_provider,
            product=product,
            model=model,
        ),
        meters=tuple(meters),
    )


def _turn_meter(
    *,
    adapter: str,
    provider: str,
    product: str,
    terminals: list[dict[str, Any]],
    events: list[LogEvent],
) -> MeteredQuantity:
    if adapter == "claude":
        quantities = [
            _optional_nonnegative_int(terminal.get("num_turns")) for terminal in terminals
        ]
        if all(quantity is not None for quantity in quantities):
            return _measured(
                provider=provider,
                product=product,
                meter="model_turns",
                quantity=sum(cast(int, quantity) for quantity in quantities),
                lineage="result.num_turns",
            )
        return _unmeasured(
            provider=provider,
            product=product,
            meter="model_turns",
            lineage="result.num_turns absent",
        )
    if adapter == "codex":
        started = sum(
            1
            for event in events
            if isinstance(event.raw, dict) and event.raw.get("type") == "turn.started"
        )
        return _measured(
            provider=provider,
            product=product,
            meter="model_turns",
            quantity=max(started, len(terminals)),
            lineage="codex turn lifecycle",
        )
    return _unmeasured(
        provider=provider,
        product=product,
        meter="model_turns",
        lineage=f"{adapter} terminal aggregate has no exact turn boundary",
    )


def _claude_server_tool_meters(
    *,
    provider: str,
    product: str,
    terminals: list[dict[str, Any]],
    events: list[LogEvent],
) -> list[MeteredQuantity]:
    relevant = {
        _canonical_tool_name(event.tool_name)
        for event in events
        if event.kind == "tool_call" and event.tool_name
    }
    out: list[MeteredQuantity] = []
    for field, meter, tool_name in (
        ("web_search_requests", "server_web_search_requests", "websearch"),
        ("web_fetch_requests", "server_web_fetch_requests", "webfetch"),
    ):
        quantities: list[int | None] = []
        present = False
        for terminal in terminals:
            usage = terminal.get("usage")
            server_tools = usage.get("server_tool_use") if isinstance(usage, dict) else None
            if isinstance(server_tools, dict) and field in server_tools:
                present = True
                quantities.append(_optional_nonnegative_int(server_tools.get(field)))
            else:
                quantities.append(None)
        if present and all(quantity is not None for quantity in quantities):
            out.append(
                _measured(
                    provider=provider,
                    product=product,
                    meter=meter,
                    quantity=sum(cast(int, quantity) for quantity in quantities),
                    lineage=f"usage.server_tool_use.{field}",
                )
            )
        elif present or tool_name in relevant:
            out.append(
                _unmeasured(
                    provider=provider,
                    product=product,
                    meter=meter,
                    lineage=f"{tool_name} tool span has no server request boundary",
                )
            )
    return out


def _canonical_tool_name(value: str | None) -> str:
    return "" if value is None else value.casefold().replace("_", "").replace("-", "")


def _measured(
    *, provider: str, product: str, meter: str, quantity: int, lineage: str
) -> MeteredQuantity:
    return MeteredQuantity(
        key=MeterKey(provider=provider, product=product, meter=meter, unit="count"),
        coverage=CoverageState.MEASURED,
        actual_quantity=quantity,
        lineage=[lineage],
    )


def _unmeasured(*, provider: str, product: str, meter: str, lineage: str) -> MeteredQuantity:
    return MeteredQuantity(
        key=MeterKey(provider=provider, product=product, meter=meter, unit="count"),
        coverage=CoverageState.UNMEASURED,
        lineage=[lineage],
    )


def _optional_nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
