"""Usage and cost tracking for metaproc log files.

Provides:
  UsageStats          — aggregatable token/cost dataclass
  load_pricing        — load pricing from YAML frontmatter in pricing.md
  compute_cost        — compute cost from tokens using pricing rates
  sum_pi_usage        — extract and sum usage from Pi CLI agent_end messages
  extract_gemini_usage — extract per-model usage from Gemini CLI stats
  aggregate_usage     — roll up usage from LogFile list by variant/model/provider
  write_usage_report  — write usage.md with YAML frontmatter + prose
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self, cast

from metaproc.io import fmf_read_frontmatter, fmf_write
from metaproc.models.pricing import PricingConfig
from metaproc.models.usage import (
    CostPair,
    CostView,
    ProviderRateLimitStats,
    ToolRunProfile,
    UsageBucket,
    UsageReport,
    usage_report_to_frontmatter,
)
from metaproc.paths import LOGS_DIR
from metaproc.plugins.discovery import get_plugin_registry

if TYPE_CHECKING:
    from metaproc.logutil.parsing import LogFile

log = logging.getLogger(__name__)

_PRICING_PATH = Path(__file__).parent.parent / "data" / "pricing.md"


@dataclass
class UsageStats:
    """Aggregatable token usage and cost data."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    cost_is_estimated: bool = False
    duration_s: float = 0.0
    tool_calls: int = 0
    model: str = ""
    provider: str = ""
    steps: int = 0
    warnings: list[str] = field(default_factory=list)

    def __iadd__(self, other: UsageStats) -> Self:
        """Accumulate usage stats."""
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.cost_usd += other.cost_usd
        self.duration_s += other.duration_s
        self.tool_calls += other.tool_calls
        self.steps += other.steps
        if other.cost_is_estimated:
            self.cost_is_estimated = True
        # Carry forward non-empty string fields (first non-empty value wins).
        if not self.model and other.model:
            self.model = other.model
        if not self.provider and other.provider:
            self.provider = other.provider
        self.warnings.extend(other.warnings)
        return self

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )


def load_pricing_config(path: Path | None = None) -> PricingConfig:
    """Load and validate the pricing table from YAML frontmatter.

    Returns a typed ``PricingConfig`` model. Raises ``ValueError`` on
    missing frontmatter or validation failure.
    """
    p = path or _PRICING_PATH
    raw = fmf_read_frontmatter(p)
    if raw is None:
        msg = f"{p}: no YAML frontmatter found"
        raise ValueError(msg)
    return PricingConfig.model_validate(raw)


def load_pricing(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load the pricing table from YAML frontmatter and return a flat ``{model: entry}`` dict.

    Each entry contains ``actual_price`` (dict of rates), optional ``list_price``
    (dict of vendor API rates), ``provider``, and metadata fields.
    """
    config = load_pricing_config(path)
    result: dict[str, dict[str, Any]] = {}
    for model_name, model_data in config.flat_lookup().items():
        entry = model_data.model_dump(exclude_none=True)
        # Flatten rate dicts for backward compatibility.
        entry["actual_price"] = model_data.actual_price.model_dump()
        if model_data.list_price is not None:
            entry["list_price"] = model_data.list_price.model_dump()
        result[model_name] = entry
    return result


def _lookup_model(model: str, pricing: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Look up model in pricing table, trying full name then basename after '/'."""
    rates = pricing.get(model)
    if rates:
        return rates
    # Pi CLI reports models with org prefix (e.g., "deepseek-ai/deepseek-v3.2-maas").
    if "/" in model:
        return pricing.get(model.rsplit("/", 1)[1])
    return None


def _get_rates(entry: dict[str, Any], *, use_list_prices: bool) -> dict[str, float]:
    """Extract the rate dict from a pricing entry.

    When *use_list_prices* is True, prefer ``list_price`` and fall back to
    ``actual_price``. Returns the selected rate dict (or empty dict).
    """
    actual = entry.get("actual_price", {})
    if use_list_prices:
        return entry.get("list_price", actual)
    return actual


def compute_cost(
    stats: UsageStats,
    pricing: dict[str, dict[str, Any]],
    *,
    use_list_prices: bool = False,
) -> float:
    """Compute cost from tokens using pricing table rates.

    When *use_list_prices* is True, use ``list_price`` rates (vendor's own
    API pricing), falling back to ``price`` when ``list_price`` is absent.

    Returns 0.0 if the model is not found in the pricing table.
    """
    entry = _lookup_model(stats.model, pricing)
    if not entry:
        return 0.0

    rates = _get_rates(entry, use_list_prices=use_list_prices)

    cost = 0.0
    cost += stats.input_tokens * rates.get("input_per_1m", 0) / 1_000_000
    cost += stats.output_tokens * rates.get("output_per_1m", 0) / 1_000_000
    cost += stats.cache_read_tokens * rates.get("cache_read_per_1m", 0) / 1_000_000
    cost += stats.cache_write_tokens * rates.get("cache_write_per_1m", 0) / 1_000_000
    return cost


def sum_pi_usage(agent_end_event: dict[str, Any]) -> UsageStats:
    """Extract and sum per-turn usage from a Pi CLI ``agent_end`` event.

    Pi CLI includes ``messages[]`` in the ``agent_end`` event, where each
    assistant message carries a ``usage`` dict with token counts and cost.
    """
    stats = UsageStats()
    messages = agent_end_event.get("messages", [])

    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        usage = msg.get("usage")
        if not usage:
            continue

        stats.input_tokens += usage.get("input", 0)
        stats.output_tokens += usage.get("output", 0)
        stats.cache_read_tokens += usage.get("cacheRead", 0)
        stats.cache_write_tokens += usage.get("cacheWrite", 0)

        cost_raw: Any = usage.get("cost", {})
        if isinstance(cost_raw, dict):
            cost_dict = cast(dict[str, Any], cost_raw)
            stats.cost_usd += cost_dict.get("total", 0.0)

        # Capture model/provider from the first assistant message that has them
        if not stats.model:
            stats.model = msg.get("model", "")
        if not stats.provider:
            stats.provider = msg.get("provider", "")

    return stats


def sum_codex_usage(turn_completed_event: dict[str, Any]) -> UsageStats:
    """Extract per-turn usage from a codex-cli 0.124.0 ``turn.completed`` event.

    codex-cli 0.124.0 reports usage inline on the terminal ``turn.completed``
    event as ``usage.{input_tokens, cached_input_tokens, output_tokens}``
    (reasoning tokens are rolled into ``output_tokens`` by the server in this
    release; older 0.31.0-era ``info.total_token_usage`` + separate
    ``reasoning_output_tokens`` / ``total_tokens`` fields are gone).

    Sets ``provider="openai"`` for cost-rollup routing.
    """
    stats = UsageStats(provider="openai")
    usage_raw: Any = turn_completed_event.get("usage", {})
    if not isinstance(usage_raw, dict):
        return stats
    usage = cast(dict[str, Any], usage_raw)
    total_input = int(usage.get("input_tokens", 0) or 0)
    cached_input = int(usage.get("cached_input_tokens", 0) or 0)
    # Codex reports `cached_input_tokens` as a SUBSET of `input_tokens` (its own
    # `non_cached_input()` is the difference). Copying both through verbatim
    # charges the cached portion twice: once at the full input rate and again at
    # the cache rate. Split it here so downstream pricing sees disjoint buckets.
    stats.input_tokens = max(total_input - cached_input, 0)
    stats.cache_read_tokens = cached_input
    stats.output_tokens = int(usage.get("output_tokens", 0) or 0)
    return stats


def extract_gemini_usage(stats_dict: dict[str, Any]) -> list[UsageStats]:
    """Extract per-model usage from a Gemini CLI ``stats`` dict.

    Gemini reports an aggregate plus per-model breakdown in ``stats.models``.
    Its ``input_tokens`` includes cached tokens, while ``input`` is the uncached
    quantity. Its ``output_tokens`` omits hidden thinking tokens, which remain in
    ``total_tokens`` and are billed as output. Normalize those fields into mutually
    exclusive uncached input, cached input, and billed generated output quantities.
    Returns a list of ``UsageStats``, one per model. If no per-model breakdown
    exists, returns a single entry with the aggregate.
    """
    models: dict[str, Any] = stats_dict.get("models", {})

    if models:
        result: list[UsageStats] = []
        for model_name, model_stats in models.items():
            uncached_input, billed_output, cached_input = _gemini_billed_tokens(model_stats)
            us = UsageStats(
                input_tokens=uncached_input,
                output_tokens=billed_output,
                cache_read_tokens=cached_input,
                model=model_name,
                provider="google",
            )
            result.append(us)
        return result

    # No per-model breakdown — use aggregate
    uncached_input, billed_output, cached_input = _gemini_billed_tokens(stats_dict)
    return [
        UsageStats(
            input_tokens=uncached_input,
            output_tokens=billed_output,
            cache_read_tokens=cached_input,
            tool_calls=stats_dict.get("tool_calls", 0),
            provider="google",
        )
    ]


def _gemini_billed_tokens(stats: dict[str, Any]) -> tuple[int, int, int]:
    total_input = int(stats.get("input_tokens", 0) or 0)
    cached_input = int(stats.get("cached", 0) or 0)
    uncached_raw = stats.get("input")
    uncached_input = (
        int(uncached_raw) if uncached_raw is not None else max(total_input - cached_input, 0)
    )
    visible_output = int(stats.get("output_tokens", 0) or 0)
    total_tokens = int(stats.get("total_tokens", 0) or 0)
    billed_output = max(visible_output, total_tokens - total_input, 0)
    return uncached_input, billed_output, cached_input


# ── Aggregation ─────────────────────────────────────────────────


@dataclass
class _DualCostAccum:
    """Accumulator for shared stats plus dual cost views."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    duration_s: float = 0.0
    tool_calls: int = 0
    steps: int = 0
    model: str = ""
    provider: str = ""
    actual_cost: float = 0.0
    actual_is_estimated: bool = False
    list_cost: float = 0.0
    list_is_estimated: bool = False
    _model_tokens: dict[str, int] = field(default_factory=dict)
    _model_provider: dict[str, str] = field(default_factory=dict)

    def add(
        self,
        us: UsageStats,
        *,
        actual: float,
        actual_estimated: bool,
        list_: float,
        list_estimated: bool,
        include_timing: bool = False,
    ) -> None:
        self.input_tokens += us.input_tokens
        self.output_tokens += us.output_tokens
        self.cache_read_tokens += us.cache_read_tokens
        self.cache_write_tokens += us.cache_write_tokens
        self.steps += 1
        if include_timing:
            self.duration_s += us.duration_s
            self.tool_calls += us.tool_calls
        # Track per-model token contribution to pick the dominant model.
        if us.model:
            tokens = us.input_tokens + us.output_tokens
            self._model_tokens[us.model] = self._model_tokens.get(us.model, 0) + tokens
            if us.provider:
                self._model_provider[us.model] = us.provider
        self.actual_cost += actual
        if actual_estimated:
            self.actual_is_estimated = True
        self.list_cost += list_
        if list_estimated:
            self.list_is_estimated = True

    def _resolve_model_provider(self) -> None:
        """Set model/provider from the model that contributed the most tokens."""
        if self._model_tokens:
            top_model = max(self._model_tokens, key=self._model_tokens.__getitem__)
            self.model = top_model
            self.provider = self._model_provider.get(top_model, "")

    def to_bucket(self) -> UsageBucket:
        self._resolve_model_provider()
        actual_view = CostView(
            cost_usd=self.actual_cost or None,
            is_estimated=self.actual_is_estimated,
        )
        list_view = CostView(
            cost_usd=self.list_cost or None,
            is_estimated=self.list_is_estimated,
        )
        return UsageBucket(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cache_read_tokens=self.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens,
            duration_s=self.duration_s,
            tool_calls=self.tool_calls,
            steps=self.steps,
            model=self.model,
            provider=self.provider,
            cost=CostPair(actual=actual_view, list=list_view),
        )


def _bin_rate_limit_events(
    log_files: list[LogFile],
) -> list[ProviderRateLimitStats]:
    """Bin rate-limit events captured on each LogFile by (provider, adapter, variant).

    Only ``is_error`` rate-limit events (``status=blocked``) count — allowed events
    are tracked as informational noise but don't contribute to the operator signal.
    """
    bins: dict[tuple[str, str, str], int] = defaultdict(int)
    for lf in log_files:
        variant = (
            lf.path.parent.parent.name if lf.path.parent.name == LOGS_DIR else lf.path.parent.name
        )
        for ev in lf.rate_limit_events:
            if not ev.is_error:
                continue
            provider = ev.provider or "unknown"
            key = (provider, ev.adapter, variant)
            bins[key] += 1
    return [
        ProviderRateLimitStats(provider=p, adapter=a, variant=v, count=c)
        for (p, a, v), c in sorted(bins.items())
    ]


def aggregate_usage(
    log_files: list[LogFile],
    pricing: dict[str, dict[str, Any]] | None = None,
) -> UsageReport:
    """Aggregate UsageStats across log files into a normalized UsageReport.

    Single pass: accumulates shared stats once, computes both actual and list
    cost per entry within the same loop.
    """
    if pricing is None:
        pricing = load_pricing()

    totals = _DualCostAccum()
    by_variant: dict[str, _DualCostAccum] = defaultdict(_DualCostAccum)
    by_model: dict[str, _DualCostAccum] = defaultdict(_DualCostAccum)
    by_provider: dict[str, _DualCostAccum] = defaultdict(_DualCostAccum)
    warnings: list[str] = []

    for lf in log_files:
        if lf.usage_stats is None:
            continue
        us = lf.usage_stats

        # Resolve provider from pricing table if not set by the adapter.
        if us.model and not us.provider:
            rates = _lookup_model(us.model, pricing)
            if rates:
                us.provider = rates.get("provider", "")

        # Compute actual cost: self-reported when available, computed otherwise.
        actual_cost = us.cost_usd
        actual_estimated = us.cost_is_estimated
        if actual_cost == 0.0 and us.model:
            computed = compute_cost(us, pricing)
            if computed > 0:
                actual_cost = computed
                actual_estimated = True
            elif _lookup_model(us.model, pricing) is None:
                warnings.append(f"Unknown model '{us.model}' — cost not computed")

        # Compute list cost: always from pricing table.
        list_cost = compute_cost(us, pricing, use_list_prices=True)
        list_estimated = True
        if list_cost == 0.0 and _lookup_model(us.model, pricing) is None:
            warnings.append(f"Unknown model '{us.model}' — list cost not computed")

        totals.add(
            us,
            actual=actual_cost,
            actual_estimated=actual_estimated,
            list_=list_cost,
            list_estimated=list_estimated,
            include_timing=True,
        )

        variant = (
            lf.path.parent.parent.name if lf.path.parent.name == LOGS_DIR else lf.path.parent.name
        )
        by_variant[variant].add(
            us,
            actual=actual_cost,
            actual_estimated=actual_estimated,
            list_=list_cost,
            list_estimated=list_estimated,
            include_timing=True,
        )

        if us.model:
            by_model[us.model].add(
                us,
                actual=actual_cost,
                actual_estimated=actual_estimated,
                list_=list_cost,
                list_estimated=list_estimated,
            )

        if us.provider:
            by_provider[us.provider].add(
                us,
                actual=actual_cost,
                actual_estimated=actual_estimated,
                list_=list_cost,
                list_estimated=list_estimated,
            )

    return UsageReport(
        run_id="",  # Caller fills in run_id, phase, generated.
        phase="",
        generated="",
        totals=totals.to_bucket(),
        by_variant={k: v.to_bucket() for k, v in sorted(by_variant.items())},
        by_model={k: v.to_bucket() for k, v in sorted(by_model.items())},
        by_provider={k: v.to_bucket() for k, v in sorted(by_provider.items())},
        rate_limit_stats=_bin_rate_limit_events(log_files),
        warnings=sorted(set(warnings)),
    )


# ── Report output ───────────────────────────────────────────────


def _fmt_tokens(n: int) -> str:
    """Format token count as human-readable string."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def _fmt_cost(c: float) -> str:
    return f"${c:.2f}"


def write_usage_report(
    output_path: Path,
    run_id: str,
    phase: str,
    log_files: list[LogFile],
    pricing: dict[str, dict[str, Any]] | None = None,
    tool_event_files: list[Path] | None = None,
    phase_dir: Path | None = None,
) -> Path:
    """Write a ``usage.md`` report with YAML frontmatter + prose summary.

    When ``tool_event_files`` is provided, each file is parsed and folded into
    the report's per-variant ``tool_profiles``. ``phase_dir`` is required in that
    case so variants can be extracted as the first path segment relative to it.

    Returns the path written.
    """
    report = aggregate_usage(log_files, pricing)
    report.run_id = run_id
    report.phase = phase
    report.generated = datetime.now(tz=UTC).isoformat()

    if tool_event_files:
        if phase_dir is None:
            raise ValueError("phase_dir is required when tool_event_files is provided")
        resolved_phase_dir = phase_dir

        def _variant_from_path(path: Path) -> str:
            return path.relative_to(resolved_phase_dir).parts[0]

        profiles: dict[str, ToolRunProfile] = {}
        for source in get_plugin_registry().tool_profile_sources:
            matching = [path for path in tool_event_files if source.matches(path)]
            if not matching:
                continue
            for key, profile in source.aggregate(
                matching,
                variant_fn=_variant_from_path,
            ).items():
                if key in profiles:
                    raise ValueError(f"multiple tool-profile sources produced profile key {key!r}")
                profiles[key] = profile
        report.tool_profiles = profiles

    metadata = usage_report_to_frontmatter(report)
    totals = report.totals

    # Build prose summary.
    actual_cost = totals.cost.actual.cost_usd or 0.0
    list_cost = totals.cost.list.cost_usd or 0.0
    cost_str = _fmt_cost(actual_cost)
    list_cost_str = _fmt_cost(list_cost)
    input_str = _fmt_tokens(totals.input_tokens)
    output_str = _fmt_tokens(totals.output_tokens)
    steps = totals.steps
    variants = len(report.by_variant)
    estimated = " (includes estimated costs)" if totals.cost.actual.is_estimated else ""

    lines = [
        f"# Usage Report: {run_id} / {phase}",
        "",
        f"Generated: {report.generated}",
        "",
        "## Summary",
        "",
        f"Total actual cost: **{cost_str}**{estimated}.",
        f"Total list cost: **{list_cost_str}** (vendor API rates).",
        f"Total tokens: {input_str} input, {output_str} output.",
        f"{steps} steps across {variants} variants.",
        "",
    ]

    # Provider breakdown.
    if report.by_provider:
        lines.append("## Cost by Provider")
        lines.append("")
        lines.append("| Provider | Actual Cost | List Cost | Input tokens | Output tokens |")
        lines.append("| --- | --- | --- | --- | --- |")
        for prov, pdata in sorted(report.by_provider.items()):
            lines.append(
                f"| {prov} | {_fmt_cost(pdata.cost.actual.cost_usd or 0)} "
                f"| {_fmt_cost(pdata.cost.list.cost_usd or 0)} "
                f"| {_fmt_tokens(pdata.input_tokens)} "
                f"| {_fmt_tokens(pdata.output_tokens)} |"
            )
        lines.append("")

    # Model breakdown.
    if report.by_model:
        lines.append("## Cost by Model")
        lines.append("")
        lines.append("| Model | Provider | Actual Cost | List Cost | Input | Output | Steps |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for model, mdata in sorted(report.by_model.items()):
            lines.append(
                f"| {model} "
                f"| {mdata.provider or '?'} "
                f"| {_fmt_cost(mdata.cost.actual.cost_usd or 0)} "
                f"| {_fmt_cost(mdata.cost.list.cost_usd or 0)} "
                f"| {_fmt_tokens(mdata.input_tokens)} "
                f"| {_fmt_tokens(mdata.output_tokens)} "
                f"| {mdata.steps} |"
            )
        lines.append("")

    # Tool-use by Variant (scorecard columns: tool_fail%, cutoff_disc%, rate_lim/rec).
    if report.tool_profiles:
        rate_lim_by_variant: dict[str, int] = defaultdict(int)
        for rl in report.rate_limit_stats:
            rate_lim_by_variant[rl.variant] += rl.count
        lines.append("## Tool-use by Variant")
        lines.append("")
        lines.append(
            "| Variant | Records | Tool calls | Tool fail% | Cutoff disc% | "
            "Native web search | Rate-lim/rec |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for variant, profile in sorted(report.tool_profiles.items()):
            lines.append(_format_tool_profile_row(variant, profile, rate_lim_by_variant))
        lines.append("")

    # Warnings.
    if report.warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in report.warnings:
            lines.append(f"- {w}")
        lines.append("")

    content = "\n".join(lines)
    fmf_write(output_path, content, metadata)
    return output_path


def _format_tool_profile_row(
    variant: str,
    profile: ToolRunProfile,
    rate_lim_by_variant: dict[str, int],
) -> str:
    total_calls = sum(stats.calls for stats in profile.per_tool.values())
    total_failures = sum(sum(stats.failures.values()) for stats in profile.per_tool.values())
    tool_fail_pct = (total_failures / total_calls * 100) if total_calls else 0.0
    cutoff = f"{profile.cutoff_disc_pct:.1f}%" if profile.cutoff_disc_pct is not None else "—"
    if profile.total_configs > 0 and profile.native_web_search_configs == profile.total_configs:
        native_web_search = "on"
    elif profile.native_web_search_configs > 0:
        native_web_search = f"{profile.native_web_search_configs}/{profile.total_configs}"
    else:
        native_web_search = "off"
    rate_lim_per_rec = (
        rate_lim_by_variant.get(variant, 0) / profile.records if profile.records else 0.0
    )
    return (
        f"| {variant} | {profile.records} | {total_calls} "
        f"| {tool_fail_pct:.1f}% | {cutoff} | {native_web_search} "
        f"| {rate_lim_per_rec:.2f} |"
    )
