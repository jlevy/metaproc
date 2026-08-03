"""Write the self-describing, human-readable resource usage summary."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from frontmatter_format import fmf_write
from strif import atomic_output_file

from metaproc.models.resource_budget import BudgetStatus
from metaproc.models.resource_summary import (
    RESOURCE_USAGE_SUMMARY_CONTRACT,
    ResourceUsageSummary,
)
from metaproc.models.resources import MeterRollup, ResourcesDocument

RESOURCE_USAGE_SUMMARY_FILE = "resource-usage-summary.md"
RESOURCE_USAGE_SCHEMA_RELATIVE = ".state/schemas/resource-usage-summary.v1.schema.yaml"


def write_resource_usage_summary(document: ResourcesDocument, run_dir: Path) -> Path:
    """Persist a validated-shape summary and its compiled schema sidecar."""
    if document.finalization is None:
        raise ValueError("resource usage summary requires finalization metadata")

    summary = ResourceUsageSummary(
        run_id=document.run_id,
        totals=document.hierarchy_root.total_metrics,
        provider_meters=document.meter_rollups,
        coverage_gaps=document.coverage_gaps,
        budgets=document.budget_evaluations,
        finalization=document.finalization,
    )
    schema_path = run_dir / RESOURCE_USAGE_SCHEMA_RELATIVE
    _write_schema_sidecar(schema_path)
    target = run_dir / RESOURCE_USAGE_SUMMARY_FILE
    metadata = {
        "softschema": {
            "contract": RESOURCE_USAGE_SUMMARY_CONTRACT,
            "schema": RESOURCE_USAGE_SCHEMA_RELATIVE,
            "envelope": "resource_usage",
            "status": "enforced",
        },
        "resource_usage": summary.model_dump(mode="json"),
    }
    with atomic_output_file(target) as tmp_path:
        fmf_write(Path(tmp_path), _render_summary(summary), metadata)
    return target


def _write_schema_sidecar(target: Path) -> None:
    source = resources.files("metaproc").joinpath(
        "data",
        "schemas",
        "resource-usage-summary.v1.schema.yaml",
    )
    content = source.read_text(encoding="utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    with atomic_output_file(target) as tmp_path:
        Path(tmp_path).write_text(content)


def _render_summary(summary: ResourceUsageSummary) -> str:
    totals = summary.totals
    lines = [
        "# Resource usage summary",
        "",
        f"Run `{summary.run_id}` finished with `{summary.finalization.state.value}`.",
        "Machine-consumed values are in the validated YAML frontmatter; this body is explanatory.",
        "",
        "| Quantity | Value |",
        "| --- | ---: |",
        f"| Input tokens | {_format_quantity(totals.input_tokens)} |",
        f"| Output tokens | {_format_quantity(totals.output_tokens)} |",
        f"| Actual cost (USD) | {_format_money(totals.actual_cost_usd)} |",
        f"| Estimated list cost (USD) | {_format_money(totals.list_cost_usd)} |",
        f"| Tool calls | {_format_quantity(totals.tool_calls)} |",
    ]
    if summary.provider_meters:
        lines.extend(
            [
                "",
                "## Provider meters",
                "",
                "| Meter | Coverage | Quantity |",
                "| --- | --- | ---: |",
            ]
        )
        for meter in summary.provider_meters:
            quantity = _meter_quantity(meter)
            lines.append(
                f"| `{'/'.join(meter.key.sort_key())}` | {meter.coverage.value} | "
                f"{_format_quantity(quantity)} |"
            )
    if summary.budgets:
        lines.extend(["", "## Budgets", ""])
        for evaluation in summary.budgets:
            marker = "⚠" if evaluation.status in {BudgetStatus.NEAR, BudgetStatus.EXCEEDED} else "-"
            lines.append(f"{marker} `{evaluation.budget.budget_id}`: {evaluation.message}")
    return "\n".join(lines) + "\n"


def _format_quantity(value: float | None) -> str:
    return "unmeasured" if value is None else f"{value:g}"


def _format_money(value: float | None) -> str:
    return "unmeasured" if value is None else f"{value:.6f}"


def _meter_quantity(meter: MeterRollup) -> float | None:
    if meter.coverage.value == "unmeasured":
        return None
    return (meter.actual_quantity or 0) + (meter.estimated_quantity or 0)
