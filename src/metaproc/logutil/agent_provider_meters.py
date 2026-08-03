"""Authoritative provider meters exposed by terminal agent-adapter records."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from metaproc.logutil.parsing import LogEvent, LogFile
from metaproc.logutil.tool_spans import pair_tool_events
from metaproc.models.resources import (
    CoverageState,
    HierarchyRef,
    MeteredQuantity,
    MeterKey,
    ProviderMeterObservation,
    ProviderRef,
)

_LLM_PRODUCT = "llm"
_REQUEST_METER = "requests"
_REQUEST_UNIT = "request"
_TURN_METER = "turns"
_TURN_UNIT = "turn"
_INVOCATION_METER = "agent_invocations"
_INVOCATION_UNIT = "invocation"


class AgentProviderMeterSource:
    """Extract exact named units without treating agent steps as API requests.

    Token and cost metrics already flow through ``LogFile.usage_stats``. This source
    adds only provider units adapter records expose explicitly: one agent invocation
    per adapter init, Claude's ``num_turns`` and server-side web-tool counts, and
    Codex's completed-turn records. Gemini reports tokens and aggregate tool calls but
    no request/turn count, so its internal turn meter is deliberately unmeasured.
    """

    name = "agent_terminal_provider_meters"

    def extract(
        self,
        *,
        log_path: Path,
        log_file: LogFile,
        log_events: Sequence[LogEvent],
        hierarchy: HierarchyRef,
        source_path: str,
    ) -> Sequence[ProviderMeterObservation]:
        del log_path, hierarchy, source_path
        adapter = log_file.parser.adapter_name if log_file.parser else ""
        if adapter == "claude":
            observations = _claude_observations(log_file, log_events)
        elif adapter == "codex":
            observations = _codex_observations(log_file, log_events)
        elif adapter == "gemini":
            observations = _gemini_observations(log_file, log_events)
        else:
            return []
        provider = {"claude": "anthropic", "codex": "openai", "gemini": "google"}[adapter]
        if _has_agent_init(adapter, log_events):
            observations.append(
                _invocation_observation(provider=provider, model=log_file.model or None)
            )
        exact_products = {observation.provider.product for observation in observations}
        observations.extend(
            _web_coverage_gaps(
                provider=provider,
                model=log_file.model or None,
                log_events=log_events,
                exact_products=exact_products,
            )
        )
        return observations


def _claude_observations(
    log_file: LogFile, log_events: Sequence[LogEvent]
) -> list[ProviderMeterObservation]:
    terminal = _last_raw_event(log_events, event_type="result")
    if terminal is None:
        return []
    model = log_file.model or None
    observations = [
        _turn_observation(
            provider="anthropic",
            model=model,
            quantity=_nonnegative_int(terminal.get("num_turns")),
        )
    ]
    usage = terminal.get("usage")
    server_tools = usage.get("server_tool_use") if isinstance(usage, dict) else None
    if not isinstance(server_tools, dict):
        return observations
    for field, product in (
        ("web_search_requests", "web_search"),
        ("web_fetch_requests", "web_fetch"),
    ):
        quantity = _nonnegative_int(server_tools.get(field))
        if quantity is not None:
            observations.append(
                _request_observation(
                    provider="anthropic",
                    product=product,
                    model=model,
                    quantity=quantity,
                )
            )
    return observations


def _codex_observations(
    log_file: LogFile, log_events: Sequence[LogEvent]
) -> list[ProviderMeterObservation]:
    completed = sum(
        1
        for event in log_events
        if isinstance(event.raw, dict) and event.raw.get("type") == "turn.completed"
    )
    attempted = any(
        isinstance(event.raw, dict)
        and event.raw.get("type") in {"turn.started", "turn.completed", "turn.failed"}
        for event in log_events
    )
    if not attempted:
        return []
    # `attempted` already establishes that turn lifecycle events exist, so zero
    # completions is a measured zero — an attempt that started and never
    # finished. Passing `completed or None` would report it as unmeasured,
    # which is the one case where the distinction matters most.
    return [
        _turn_observation(
            provider="openai",
            model=log_file.model or None,
            quantity=completed,
        )
    ]


def _gemini_observations(
    log_file: LogFile, log_events: Sequence[LogEvent]
) -> list[ProviderMeterObservation]:
    if _last_raw_event(log_events, event_type="result") is None:
        return []
    return [
        _turn_observation(
            provider="google",
            model=log_file.model or None,
            quantity=None,
        )
    ]


def _turn_observation(
    *, provider: str, model: str | None, quantity: int | None
) -> ProviderMeterObservation:
    return ProviderMeterObservation(
        provider=ProviderRef(provider=provider, product=_LLM_PRODUCT, model=model),
        meters=[
            _quantity(
                provider=provider,
                product=_LLM_PRODUCT,
                meter=_TURN_METER,
                unit=_TURN_UNIT,
                quantity=quantity,
            )
        ],
    )


def _request_observation(
    *, provider: str, product: str, model: str | None, quantity: int
) -> ProviderMeterObservation:
    return ProviderMeterObservation(
        provider=ProviderRef(provider=provider, product=product, model=model),
        api_requests=quantity,
        meters=[
            _quantity(
                provider=provider,
                product=product,
                meter=_REQUEST_METER,
                unit=_REQUEST_UNIT,
                quantity=quantity,
            )
        ],
    )


def _invocation_observation(*, provider: str, model: str | None) -> ProviderMeterObservation:
    return ProviderMeterObservation(
        provider=ProviderRef(provider=provider, product=_LLM_PRODUCT, model=model),
        meters=[
            _quantity(
                provider=provider,
                product=_LLM_PRODUCT,
                meter=_INVOCATION_METER,
                unit=_INVOCATION_UNIT,
                quantity=1,
            )
        ],
    )


def _web_coverage_gaps(
    *,
    provider: str,
    model: str | None,
    log_events: Sequence[LogEvent],
    exact_products: set[str],
) -> list[ProviderMeterObservation]:
    products = {
        product
        for span in pair_tool_events(list(log_events))
        if (product := _web_product(span.tool_name)) is not None
    }
    return [
        ProviderMeterObservation(
            provider=ProviderRef(provider=provider, product=product, model=model),
            meters=[
                _quantity(
                    provider=provider,
                    product=product,
                    meter=_REQUEST_METER,
                    unit=_REQUEST_UNIT,
                    quantity=None,
                )
            ],
        )
        for product in sorted(products - exact_products)
    ]


def _web_product(tool_name: str) -> str | None:
    normalized = tool_name.strip().casefold().replace("-", "_")
    if normalized in {"websearch", "web_search", "google_web_search"}:
        return "web_search"
    if normalized in {"webfetch", "web_fetch"}:
        return "web_fetch"
    return None


def _quantity(
    *, provider: str, product: str, meter: str, unit: str, quantity: int | None
) -> MeteredQuantity:
    return MeteredQuantity(
        key=MeterKey(provider=provider, product=product, meter=meter, unit=unit),
        coverage=CoverageState.MEASURED if quantity is not None else CoverageState.UNMEASURED,
        actual_quantity=quantity,
    )


def _last_raw_event(log_events: Sequence[LogEvent], *, event_type: str) -> dict[str, Any] | None:
    for event in reversed(log_events):
        if isinstance(event.raw, dict) and event.raw.get("type") == event_type:
            return event.raw
    return None


def _has_agent_init(adapter: str, log_events: Sequence[LogEvent]) -> bool:
    expected_types = {
        "claude": {"system"},
        "codex": {"thread.started"},
        "gemini": {"init"},
    }[adapter]
    return any(
        isinstance(event.raw, dict) and event.raw.get("type") in expected_types
        for event in log_events
    )


def _nonnegative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
