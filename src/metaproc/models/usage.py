"""Pydantic models for the normalized usage.md schema.

The normalized shape stores shared stats (tokens, duration, model, provider)
once per bucket, with only pricing nested under ``cost.actual`` / ``cost.list``.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class CostView(BaseModel):
    """A single pricing perspective (actual or list)."""

    cost_usd: float | None = None
    is_estimated: bool = False


class CostPair(BaseModel):
    """Dual-view cost: actual (self-reported) and list (vendor API rates)."""

    actual: CostView = Field(default_factory=CostView)
    list: CostView = Field(default_factory=CostView)


class UsageBucket(BaseModel):
    """Shared usage stats for a single aggregation bucket.

    Numeric fields default to 0; string fields default to empty.
    Fields that are zero/empty are omitted during serialization.

    The four ``*_node_id`` / ``item_key`` / ``worker_id`` fields are
    optional ownership stamps added by the resource-observability spec
    (resobs-B7). They let a per-session bucket carry attribution all
    the way down to the step / item / worker that produced it; bucket
    aggregations (``totals``, ``by_variant``, ``by_model``, etc.) leave
    them ``None`` because attribution is meaningless at those levels.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    duration_s: float = 0.0
    tool_calls: int = 0
    steps: int = 0
    model: str = ""
    provider: str = ""
    cost: CostPair = Field(default_factory=CostPair)
    process_node_id: str | None = None
    step_node_id: str | None = None
    item_key: str | None = None
    worker_id: str | None = None


class ToolCallStats(BaseModel):
    """Per-tool invocation stats inside a single ToolRunProfile.

    Invariant: ``calls == ok + sum(failures.values())``. The caller is
    responsible for maintaining this — models do not validate it so that
    aggregators can build up stats incrementally.
    """

    tool_name: str
    calls: int = 0
    ok: int = 0
    failures: dict[str, int] = Field(default_factory=dict)
    duration_s: float = 0.0


class ToolRunProfile(BaseModel):
    """Per-variant tool-use profile aggregated across all records in a run.

    ``cutoff_disc_pct`` is ``live_mode_configs / total_configs * 100`` when
    ``total_configs > 0``, else ``None``. It measures runbook gap B: the share of
    per-item sessions that defaulted to ``mode=live backtest_date=null`` instead
    of launching with a valid ``backtest_date`` (future-knowledge-leakage risk).
    Higher values are worse.

    ``native_web_search_configs`` is the count of sessions whose config stub had
    ``native_web_search: true`` — the presence-only signal for runbook gap A.
    Per-turn activity (how many grounding queries the model issued) is not
    captured here; see the tool-use-observability runbook for why.
    """

    variant: str
    records: int = 0
    per_tool: dict[str, ToolCallStats] = Field(default_factory=dict)
    total_configs: int = 0
    live_mode_configs: int = 0
    native_web_search_configs: int = 0
    cutoff_disc_pct: float | None = None


class ProviderRateLimitStats(BaseModel):
    """Count of rate-limit events binned by (provider, adapter, variant).

    Populated by the pi-cli log parser when it sees ``rate_limit_event`` records.
    Lets operators say "X% of W1's failures were Vertex MaaS GLM-5 rate-limits,
    not worker-local defects" from emitted data, not narrative.
    """

    provider: str
    adapter: str
    variant: str
    count: int = 0


class UsageReport(BaseModel):
    """Top-level normalized usage report."""

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    schema_: str = Field(default="metaproc:UsageReport/0.2", alias="schema")
    run_id: str
    phase: str
    generated: str
    totals: UsageBucket = Field(default_factory=UsageBucket)
    by_variant: dict[str, UsageBucket] = Field(default_factory=dict)
    by_model: dict[str, UsageBucket] = Field(default_factory=dict)
    by_provider: dict[str, UsageBucket] = Field(default_factory=dict)
    by_step: dict[str, UsageBucket] = Field(default_factory=dict)
    tool_profiles: dict[str, ToolRunProfile] = Field(default_factory=dict)
    rate_limit_stats: list[ProviderRateLimitStats] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class UsageEnvelope(BaseModel):
    """Envelope wrapper following the frontmatter document-type convention."""

    usage: UsageReport


# ── Serialization helpers ──────────────────────────────────────


def _cost_view_dict(cv: CostView) -> dict[str, object]:
    """Serialize a CostView, omitting defaults."""
    d: dict[str, object] = {}
    if cv.cost_usd is not None:
        d["cost_usd"] = round(cv.cost_usd, 3)
    if cv.is_estimated:
        d["is_estimated"] = True
    return d


def _cost_pair_dict(cp: CostPair) -> dict[str, object]:
    """Serialize a CostPair. Empty actual becomes ``{}``."""
    return {
        "actual": _cost_view_dict(cp.actual),
        "list": _cost_view_dict(cp.list),
    }


def bucket_to_dict(bucket: UsageBucket, *, include_timing: bool = True) -> dict[str, object]:
    """Serialize a UsageBucket, omitting zero/empty fields.

    ``include_timing`` controls whether ``duration_s`` and ``tool_calls``
    are included (only meaningful at totals/by_variant level).
    """
    d: dict[str, object] = {}
    if bucket.input_tokens:
        d["input_tokens"] = bucket.input_tokens
    if bucket.output_tokens:
        d["output_tokens"] = bucket.output_tokens
    if bucket.cache_read_tokens:
        d["cache_read_tokens"] = bucket.cache_read_tokens
    if bucket.cache_write_tokens:
        d["cache_write_tokens"] = bucket.cache_write_tokens
    if include_timing and bucket.duration_s:
        d["duration_s"] = bucket.duration_s
    if include_timing and bucket.tool_calls:
        d["tool_calls"] = bucket.tool_calls
    if bucket.steps:
        d["steps"] = bucket.steps
    if bucket.model:
        d["model"] = bucket.model
    if bucket.provider:
        d["provider"] = bucket.provider
    if bucket.process_node_id:
        d["process_node_id"] = bucket.process_node_id
    if bucket.step_node_id:
        d["step_node_id"] = bucket.step_node_id
    if bucket.item_key:
        d["item_key"] = bucket.item_key
    if bucket.worker_id:
        d["worker_id"] = bucket.worker_id
    d["cost"] = _cost_pair_dict(bucket.cost)
    return d


def _tool_call_stats_dict(stats: ToolCallStats) -> dict[str, object]:
    d: dict[str, object] = {"tool_name": stats.tool_name, "calls": stats.calls}
    if stats.ok:
        d["ok"] = stats.ok
    if stats.failures:
        d["failures"] = dict(sorted(stats.failures.items()))
    if stats.duration_s:
        d["duration_s"] = round(stats.duration_s, 3)
    return d


def _tool_run_profile_dict(profile: ToolRunProfile) -> dict[str, object]:
    d: dict[str, object] = {
        "variant": profile.variant,
        "records": profile.records,
    }
    if profile.per_tool:
        d["per_tool"] = {
            name: _tool_call_stats_dict(stats) for name, stats in sorted(profile.per_tool.items())
        }
    if profile.total_configs:
        d["total_configs"] = profile.total_configs
    if profile.live_mode_configs:
        d["live_mode_configs"] = profile.live_mode_configs
    if profile.native_web_search_configs:
        d["native_web_search_configs"] = profile.native_web_search_configs
    if profile.cutoff_disc_pct is not None:
        d["cutoff_disc_pct"] = round(profile.cutoff_disc_pct, 2)
    return d


def _rate_limit_stats_dict(stats: ProviderRateLimitStats) -> dict[str, object]:
    return {
        "provider": stats.provider,
        "adapter": stats.adapter,
        "variant": stats.variant,
        "count": stats.count,
    }


def usage_report_to_frontmatter(report: UsageReport) -> dict[str, object]:
    """Serialize a UsageReport into a dict suitable for YAML frontmatter."""
    d: dict[str, object] = {
        "schema": report.schema_,
        "run_id": report.run_id,
        "phase": report.phase,
        "generated": report.generated,
        "totals": bucket_to_dict(report.totals, include_timing=True),
        "by_variant": {
            k: bucket_to_dict(v, include_timing=True) for k, v in sorted(report.by_variant.items())
        },
        "by_model": {
            k: bucket_to_dict(v, include_timing=False) for k, v in sorted(report.by_model.items())
        },
        "by_provider": {
            k: bucket_to_dict(v, include_timing=False)
            for k, v in sorted(report.by_provider.items())
        },
        "warnings": report.warnings,
    }
    if report.by_step:
        d["by_step"] = {
            k: bucket_to_dict(v, include_timing=True) for k, v in sorted(report.by_step.items())
        }
    if report.tool_profiles:
        d["tool_profiles"] = {
            variant: _tool_run_profile_dict(profile)
            for variant, profile in sorted(report.tool_profiles.items())
        }
    if report.rate_limit_stats:
        d["rate_limit_stats"] = [_rate_limit_stats_dict(s) for s in report.rate_limit_stats]
    return d
