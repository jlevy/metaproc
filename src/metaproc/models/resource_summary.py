"""Typed frontmatter contract for the compact resource usage summary."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from metaproc.models.resource_budget import BudgetEvaluation, ResourceFinalization
from metaproc.models.resources import MeterKey, MeterRollup, Metrics

RESOURCE_USAGE_SUMMARY_CONTRACT = "metaproc.resources:ResourceUsageSummary/v1"


class ResourceUsageArtifacts(BaseModel):
    """Relative paths joining the human summary to its machine evidence."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    resources: str = "resources.json"
    events: str = ".logs/resource-events.jsonl"
    summary: str = "resource-usage-summary.md"


class ResourceUsageSummary(BaseModel):
    """All machine-consumed values stored in summary frontmatter."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    run_id: str
    totals: Metrics
    provider_meters: list[MeterRollup] = Field(default_factory=list)
    coverage_gaps: list[MeterKey] = Field(default_factory=list)
    budgets: list[BudgetEvaluation] = Field(default_factory=list)
    finalization: ResourceFinalization
    artifacts: ResourceUsageArtifacts = Field(default_factory=ResourceUsageArtifacts)
