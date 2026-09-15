"""Set several runs' operations summaries side by side against a per-item chain target."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from metaproc.engine.operations_summary import build_operations_summary, read_operations_summary
from metaproc.models.operations_summary import (
    OPERATIONS_SUMMARY_EXTRACTOR_VERSION,
    AgentOperationsSummary,
    OperationsRollup,
    OperationsRollupRow,
)


def build_operations_rollup(
    run_dirs: Sequence[Path],
    *,
    target_min_s: float,
    target_max_s: float,
    recompute: bool = False,
) -> OperationsRollup:
    """Return one row per run, reading each written summary unless *recompute* is set.

    A written summary from another extractor version is recomputed in memory. Nothing
    is written.
    """
    if target_min_s < 0 or target_max_s < target_min_s:
        raise ValueError("target range must satisfy 0 <= min <= max")
    rows: list[OperationsRollupRow] = []
    for run_dir in run_dirs:
        source: Literal["written", "computed"] = "computed"
        summary = None if recompute else read_operations_summary(run_dir)
        if (
            summary is not None
            and summary.extractor_version == OPERATIONS_SUMMARY_EXTRACTOR_VERSION
        ):
            source = "written"
        else:
            summary = build_operations_summary(run_dir)
        rows.append(
            rollup_row(
                run_dir,
                summary,
                source=source,
                target_min_s=target_min_s,
                target_max_s=target_max_s,
            )
        )
    return OperationsRollup(target_min_s=target_min_s, target_max_s=target_max_s, rows=rows)


def rollup_row(
    run_dir: Path,
    summary: AgentOperationsSummary,
    *,
    source: Literal["written", "computed"],
    target_min_s: float,
    target_max_s: float,
) -> OperationsRollupRow:
    """Project one summary onto the rollup columns, carrying every missing figure's reason."""
    unavailable: dict[str, str] = {}

    def need(name: str, value: object, reason: str) -> None:
        if value is None:
            unavailable[name] = reason

    run = summary.run
    items = summary.items
    resources = summary.resources
    parallelism = summary.parallelism
    retries = summary.retries

    state = run.state if run else None
    item_count = run.item_count if run else None
    elapsed = run.elapsed_s if run else None
    need("state", state, _reason(summary, "run", "state"))
    need("items", item_count, _reason(summary, "run", "item_count"))
    need("elapsed_s", elapsed, _reason(summary, "run", "elapsed_s"))

    setup_s = summary.setup.elapsed_s if summary.setup else None
    need("setup_s", setup_s, _reason(summary, "setup", "elapsed_s"))

    chain = items.chain_running_s if items else None
    chain_reason = _reason(summary, "items", "chain_running_s")
    p50 = chain.p50 if chain else None
    p90 = chain.p90 if chain else None
    maximum = chain.max if chain else None
    for name, value in (
        ("chain_running_p50_s", p50),
        ("chain_running_p90_s", p90),
        ("chain_running_max_s", maximum),
    ):
        need(name, value, chain_reason)

    under = within = over = None
    if items is not None and chain is not None:
        timed = [c.chain_running_s for c in items.items if c.chain_running_s is not None]
        under = sum(1 for value in timed if value < target_min_s)
        within = sum(1 for value in timed if target_min_s <= value <= target_max_s)
        over = sum(1 for value in timed if value > target_max_s)
    for name, value in (
        ("chains_under_target", under),
        ("chains_within_target", within),
        ("chains_over_target", over),
    ):
        need(name, value, chain_reason)

    per_item: float | None = None
    if elapsed is not None and item_count:
        per_item = round(elapsed / item_count, 3)
    else:
        need("elapsed_per_item_s", per_item, "run elapsed or item count is unavailable or zero")

    list_cost = resources.list_cost_usd if resources else None
    need("list_cost_usd", list_cost, _reason(summary, "resources", "list_cost_usd"))
    unpriced = sum(entry.invocations for entry in resources.unpriced_models) if resources else 0
    cost_per_item: float | None = None
    if list_cost is not None and item_count:
        cost_per_item = round(list_cost / item_count, 6)
    else:
        need(
            "list_cost_per_item_usd",
            cost_per_item,
            "list cost or item count is unavailable or zero",
        )

    peak = parallelism.peak_running if parallelism else None
    mean = parallelism.mean_running if parallelism else None
    ceiling = parallelism.ceiling if parallelism else None
    need("peak_running", peak, _reason(summary, "parallelism", "peak_running"))
    need("mean_running", mean, _reason(summary, "parallelism", "mean_running"))
    need("ceiling", ceiling, _reason(summary, "parallelism", "ceiling"))

    rss = resources.rss_bytes_max if resources else None
    swap = resources.swap_used_peak_gb if resources else None
    need("rss_bytes_max", rss, _reason(summary, "resources", "rss_bytes_max"))
    need("swap_used_peak_gb", swap, _reason(summary, "resources", "swap_used_peak_gb"))

    retry_count = retries.retries if retries else None
    not_succeeded: int | None = None
    if retries is not None:
        not_succeeded = sum(
            count
            for disposition, count in retries.by_disposition.items()
            if disposition != "succeeded"
        )
    need("retries", retry_count, _reason(summary, "retries", None))
    need("not_succeeded", not_succeeded, _reason(summary, "retries", None))

    return OperationsRollupRow(
        run_dir=str(run_dir),
        run_id=summary.run_id,
        state=state,
        items=item_count,
        elapsed_s=elapsed,
        setup_s=setup_s,
        chain_running_p50_s=p50,
        chain_running_p90_s=p90,
        chain_running_max_s=maximum,
        chains_under_target=under,
        chains_within_target=within,
        chains_over_target=over,
        elapsed_per_item_s=per_item,
        list_cost_usd=list_cost,
        list_cost_per_item_usd=cost_per_item,
        unpriced_invocations=unpriced,
        peak_running=peak,
        mean_running=mean,
        ceiling=ceiling,
        rss_bytes_max=rss,
        swap_used_peak_gb=swap,
        retries=retry_count,
        not_succeeded=not_succeeded,
        summary_source=source,
        unavailable=unavailable,
    )


def _reason(summary: AgentOperationsSummary, section: str, name: str | None) -> str:
    value = getattr(summary, section)
    if value is None:
        return f"{section} section unavailable: {summary.unavailable.get(section, 'not recorded')}"
    if name is None:
        return f"{section} is unavailable"
    unavailable = getattr(value, "unavailable", {})
    return unavailable.get(name, f"{section}.{name} is unavailable")
