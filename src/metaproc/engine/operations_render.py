"""Markdown presentation of the operations summary and the cross-run rollup.

The body is explanatory: every figure it shows comes from the structured summary, and
nothing reads values back out of it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from pydantic import BaseModel

from metaproc.io.markdown_table import render_markdown_table
from metaproc.models.operations_summary import (
    AgentOperationsSummary,
    Distribution,
    ItemChain,
    OperationsRollup,
    PoolRow,
)

NOT_AVAILABLE = "n/a"
_SECTIONS = ("run", "setup", "items", "steps", "parallelism", "retries", "agents", "resources")


def render_summary_markdown(summary: AgentOperationsSummary) -> str:
    """Render the full explanatory body for one run."""
    lines: list[str] = ["# Operations Summary", ""]
    run = summary.run
    state = run.state if run and run.state else NOT_AVAILABLE
    lines.append(
        f"Run `{summary.run_id}` ended `{state}`. Machine-consumed values are in the "
        "validated YAML frontmatter; this body is explanatory, and `n/a` marks a figure "
        "listed under Unavailable Figures."
    )
    lines.extend(_run_section(summary))
    lines.extend(_stage_section(summary))
    lines.extend(_item_section(summary))
    lines.extend(_step_section(summary))
    lines.extend(_parallelism_section(summary))
    lines.extend(_retry_section(summary))
    lines.extend(_agent_section(summary))
    lines.extend(_resource_section(summary))
    lines.extend(_unavailable_section(summary))
    return "\n".join(lines).rstrip() + "\n"


def render_rollup_markdown(rollup: OperationsRollup) -> str:
    """Render one row per run against the per-item chain target."""
    target = f"{_minutes(rollup.target_min_s)} to {_minutes(rollup.target_max_s)}"
    headers = [
        "Run",
        "State",
        "Items",
        "Elapsed",
        "Setup",
        "Chain p50",
        "Chain p90",
        "Chain max",
        "Under / within / over",
        "Elapsed per item",
        "List cost per item",
        "Peak / mean running / ceiling",
        "Peak RSS",
        "Swap peak",
        "Retries / not succeeded",
    ]
    rows = [
        [
            row.run_id.rsplit("/", 1)[-1],
            row.state or NOT_AVAILABLE,
            _count(row.items),
            _minutes(row.elapsed_s),
            _minutes(row.setup_s),
            _minutes(row.chain_running_p50_s),
            _minutes(row.chain_running_p90_s),
            _minutes(row.chain_running_max_s),
            " / ".join(
                _count(value)
                for value in (
                    row.chains_under_target,
                    row.chains_within_target,
                    row.chains_over_target,
                )
            ),
            _minutes(row.elapsed_per_item_s),
            _list_cost(row.list_cost_per_item_usd, lower_bound=row.unpriced_invocations > 0),
            f"{_count(row.peak_running)} / {_number(row.mean_running, 1)} / {_count(row.ceiling)}",
            _gib(row.rss_bytes_max),
            _gb(row.swap_used_peak_gb),
            f"{_count(row.retries)} / {_count(row.not_succeeded)}",
        ]
        for row in rollup.rows
    ]
    lines = [
        "# Operations Rollup",
        "",
        f"Chain running time is compared against a per-item target of {target}.",
        "",
        render_markdown_table(headers, rows, align=["left", "left", *["right"] * 13]),
    ]
    if any(row.unpriced_invocations for row in rollup.rows):
        lines.extend(
            [
                "",
                (
                    "A list cost marked `at least` leaves out invocations whose model has no "
                    "list price."
                ),
            ]
        )
    notes = [
        f"- `{row.run_id}`: {name}: {reason}"
        for row in rollup.rows
        for name, reason in row.unavailable.items()
    ]
    if notes:
        lines.extend(["", "## Unavailable Figures", "", *notes])
    return "\n".join(lines) + "\n"


# ── Sections ──────────────────────────────────────────────────────


def _run_section(summary: AgentOperationsSummary) -> list[str]:
    run = summary.run
    lines = ["", "## Run", ""]
    if run is None:
        return [*lines, _missing_section(summary, "run")]
    setup = summary.setup
    revisions = ", ".join(f"{key} `{value}`" for key, value in (run.revisions or {}).items())
    setup_text = NOT_AVAILABLE
    if setup is not None and setup.elapsed_s is not None:
        setup_text = f"{_seconds(setup.elapsed_s)} ({_pct(setup.share_of_elapsed)})"
    rows = [
        ["Process", _text(run.process)],
        ["State", f"{_text(run.state)} (from {_text(run.state_source)})"],
        ["Started", _text(run.started_at)],
        ["Ended", _text(run.ended_at)],
        ["Elapsed", f"{_minutes(run.elapsed_s)} ({_seconds(run.elapsed_s)})"],
        ["Items", _count(run.item_count)],
        ["Variant", _text(run.variant)],
        ["Execution profile", _text(run.execution_profile)],
        ["Backend", _text(run.backend)],
        ["Revisions", revisions or NOT_AVAILABLE],
        ["Setup", setup_text],
        ["Extraction", _seconds(summary.extraction_s, digits=2)],
    ]
    lines.append(render_markdown_table(["Figure", "Value"], rows))
    if setup is not None and setup.step_ids:
        lines.extend(["", "Setup steps: " + ", ".join(f"`{s}`" for s in setup.step_ids) + "."])
    return lines


def _stage_section(summary: AgentOperationsSummary) -> list[str]:
    lines = ["", "## Stages", ""]
    if summary.stages is None:
        return [*lines, _missing_section(summary, "stages")]
    rows = [
        [
            f"`{row.step_id}`",
            _text(row.task_shape),
            _count(row.item_count),
            _text(row.state),
            _seconds(row.elapsed_s),
            _pct(row.share_of_elapsed),
        ]
        for row in summary.stages
    ]
    lines.append(
        render_markdown_table(
            ["Step", "Shape", "Items", "State", "Elapsed", "Share"],
            rows,
            align=["left", "left", "right", "left", "right", "right"],
        )
    )
    return lines


def _item_section(summary: AgentOperationsSummary) -> list[str]:
    lines = ["", "## Per Item", ""]
    items = summary.items
    if items is None:
        return [*lines, _missing_section(summary, "items")]
    lines.append(
        f"{items.item_count} items across mapped stages "
        + ", ".join(f"`{stage}`" for stage in items.stages)
        + ". Chain running time sums each item's running time per stage; barrier wait is "
        "the time between an item's completion in one stage and its start in the next."
    )
    lines.append("")
    lines.append(
        _distribution_table(
            [
                ("Chain running", items.chain_running_s),
                ("Barrier wait", items.barrier_wait_s),
                ("Chain span", items.chain_span_s),
            ],
            minutes=True,
        )
    )
    lines.extend(["", "### Per Stage", ""])
    stage_rows: list[tuple[str, Distribution | None]] = []
    for stage in items.per_stage:
        stage_rows.append((f"`{stage.stage}` item", stage.running_s))
        stage_rows.extend(
            (f"`{stage.stage}` / `{step.step_id}`", step.elapsed_s) for step in stage.steps
        )
    lines.append(_distribution_table(stage_rows, minutes=True))
    if items.slowest:
        lines.extend(["", "### Slowest Items", ""])
        lines.append(
            render_markdown_table(
                ["Item", "Chain running", "Barrier wait", "Chain span", "Slowest step"],
                [_slowest_row(chain) for chain in items.slowest],
                align=["left", "right", "right", "right", "left"],
            )
        )
    return lines


def _step_section(summary: AgentOperationsSummary) -> list[str]:
    lines = ["", "## Steps", ""]
    steps = summary.steps
    if steps is None:
        return [*lines, _missing_section(summary, "steps")]
    ignored = (
        f" ({_plural(steps.ignored_state_dirs, 'copied `.state` directory', 'copied `.state` directories')} ignored)"
        if steps.ignored_state_dirs
        else ""
    )
    lines.append(
        f"{len(steps.rows)} step types across {steps.scope_count} scopes{ignored}. Agent steps total "
        f"{_seconds(steps.agent_total_s)}; code steps total {_seconds(steps.code_total_s)}. "
        "A mapped step contributes one sample per item."
    )
    if steps.unreadable_plans:
        lines.extend(
            [
                "",
                "Run plans that could not be read; their scopes count, without step modes:",
                "",
                *(f"- `{path}`: {reason}" for path, reason in steps.unreadable_plans.items()),
            ]
        )
    lines.append("")
    rows = [
        [
            f"`{row.step_id}`",
            f"`{row.process}`",
            _text(row.mode),
            str(row.elapsed_s.count),
            _seconds(row.elapsed_s.p50),
            _seconds(row.elapsed_s.p90),
            _seconds(row.elapsed_s.max),
            _seconds(row.elapsed_s.total),
        ]
        for row in steps.rows
    ]
    lines.append(
        render_markdown_table(
            ["Step", "Process", "Mode", "Count", "p50", "p90", "Max", "Total"],
            rows,
            align=["left", "left", "left", "right", "right", "right", "right", "right"],
        )
    )
    return lines


def _parallelism_section(summary: AgentOperationsSummary) -> list[str]:
    lines = ["", "## Parallelism", ""]
    parallelism = summary.parallelism
    if parallelism is None:
        return [*lines, _missing_section(summary, "parallelism")]
    if parallelism.sample_source == "pressure_check":
        samples = _plural(parallelism.pressure_checks, "pressure check", "pressure checks")
    else:
        samples = _plural(parallelism.health_samples, "health sample", "health samples")
    lines.append(
        f"Peak {_count(parallelism.peak_running)} and time-weighted mean "
        f"{_number(parallelism.mean_running, 1)} running pooled tasks against a ceiling of "
        f"{_count(parallelism.ceiling)}; {_pct(parallelism.share_samples_at_cap)} of "
        f"{samples} were at the current cap and "
        f"{_pct(parallelism.share_samples_at_ceiling)} at the ceiling."
    )
    if parallelism.empty_streams:
        lines.extend(
            [
                "",
                (
                    f"{_plural(parallelism.empty_streams, 'other event stream', 'other event streams')}"
                    ", such as agent step admission streams, started no pool or process."
                ),
            ]
        )
    lines.append("")
    rows = [
        [
            f"`{pool.source}`",
            _count(pool.ceiling),
            str(pool.process_starts),
            _count(pool.peak_running),
            _number(pool.mean_running, 1),
            f"{_count(pool.cap_min)} to {_count(pool.cap_max)}",
            _pool_samples(pool),
            _pct(pool.share_samples_at_cap),
            _pct(pool.share_samples_at_ceiling),
        ]
        for pool in parallelism.pools
    ]
    lines.append(
        render_markdown_table(
            [
                "Pool stream",
                "Ceiling",
                "Starts",
                "Peak",
                "Mean",
                "Cap range",
                "Samples",
                "At cap",
                "At ceiling",
            ],
            rows,
            align=["left", *["right"] * 8],
        )
    )
    return lines


def _retry_section(summary: AgentOperationsSummary) -> list[str]:
    lines = ["", "## Retries", ""]
    retries = summary.retries
    if retries is None:
        return [*lines, _missing_section(summary, "retries")]
    lines.append(
        f"{retries.attempt_records} attempt records ({_counts(retries.path_shapes)}), "
        f"{retries.retries} of them retries; "
        f"{retries.live_attempts} without a disposition; {retries.non_record_attempt_files} "
        f"other `attempt.yaml` files; {retries.unreadable_records} unreadable. Attempts that "
        f"wrote nothing: {_count(retries.wrote_nothing)}."
    )
    lines.append("")
    lines.append(f"By disposition: {_counts(retries.by_disposition)}.")
    lines.append("")
    lines.append(f"By failure class: {_counts(retries.by_failure_class)}.")
    if retries.by_step:
        lines.append("")
        lines.append(
            render_markdown_table(
                ["Step", "Process", "Attempts", "Not succeeded", "Failure classes"],
                [
                    [
                        f"`{row.step_id}`",
                        f"`{row.process}`",
                        str(row.attempts),
                        str(row.not_succeeded),
                        _counts(row.by_failure_class),
                    ]
                    for row in retries.by_step
                ],
                align=["left", "left", "right", "right", "left"],
            )
        )
    return lines


def _agent_section(summary: AgentOperationsSummary) -> list[str]:
    lines = ["", "## Agents", ""]
    agents = summary.agents
    if agents is None:
        return [*lines, _missing_section(summary, "agents")]
    tokens = agents.tokens
    rows = [
        ["Transcripts", str(agents.transcripts)],
        ["Transcripts without a terminal result", str(agents.transcripts_without_result)],
        ["Provider time", f"{_minutes(agents.provider_s)} ({_seconds(agents.provider_s)})"],
        ["Requested models", _counts(agents.requested_models)],
        ["Served models", _counts(agents.served_models)],
        ["Invocations missing the requested model", _count(agents.model_mismatches)],
        ["Input tokens", _count(tokens.input_tokens if tokens else None)],
        ["Output tokens", _count(tokens.output_tokens if tokens else None)],
        ["Cache read tokens", _count(tokens.cache_read_tokens if tokens else None)],
        ["Cache write tokens", _count(tokens.cache_write_tokens if tokens else None)],
        ["Tool calls", _count(agents.tool_calls)],
        ["Tool results at the 16 MiB cap", _count(agents.tool_results_at_cap)],
    ]
    lines.append(render_markdown_table(["Figure", "Value"], rows, align=["left", "right"]))
    if agents.meters:
        lines.append("")
        lines.append(
            render_markdown_table(
                ["Meter", "Coverage", "Quantity"],
                [
                    [
                        f"`{'/'.join(meter.key.sort_key())}`",
                        meter.coverage.value,
                        _number(
                            meter.actual_quantity
                            if meter.actual_quantity is not None
                            else meter.estimated_quantity,
                            0,
                        ),
                    ]
                    for meter in agents.meters
                ],
                align=["left", "left", "right"],
            )
        )
    if agents.oversized_transcripts:
        lines.extend(["", "Transcripts of at least 16 MiB:", ""])
        lines.extend(f"- `{path}`" for path in agents.oversized_transcripts)
    return lines


def _resource_section(summary: AgentOperationsSummary) -> list[str]:
    lines = ["", "## Resources", ""]
    res = summary.resources
    if res is None:
        return [*lines, _missing_section(summary, "resources")]
    rows = [
        ["List cost", _list_cost(res.list_cost_usd, lower_bound=bool(res.unpriced_models))],
        ["Actual cost", _money(res.actual_cost_usd)],
        ["CPU average", _number(res.cpu_pct_avg, 1, suffix="%")],
        ["CPU peak", _number(res.cpu_pct_max, 1, suffix="%")],
        ["Peak RSS", _gib(res.rss_bytes_max)],
        ["Swap peak", _gb(res.swap_used_peak_gb)],
        ["Swap growth over the run", _gb(res.swap_growth_gb)],
        ["Peak swap growth rate", _number(res.swap_delta_max_gb_per_min, 2, suffix=" GB/min")],
        ["Minimum free disk", _gb(res.disk_free_min_gb)],
        ["Run size on disk", _mib(res.run_size_bytes)],
        ["Run files", _count(res.run_file_count)],
        ["Health samples", str(res.health_samples)],
        ["Resource finalization", _text(res.resource_finalization_state)],
    ]
    lines.append(render_markdown_table(["Figure", "Value"], rows, align=["left", "right"]))
    if res.unpriced_models:
        left_out = sum(entry.invocations for entry in res.unpriced_models)
        lines.extend(
            [
                "",
                (
                    f"The list cost leaves out {_plural(left_out, 'invocation', 'invocations')} "
                    "with token usage whose model has no list price, so it is a lower bound."
                ),
                "",
                render_markdown_table(
                    ["Model", "Invocations"],
                    [
                        [
                            f"`{entry.model}`" if entry.model else "(no model named)",
                            str(entry.invocations),
                        ]
                        for entry in res.unpriced_models
                    ],
                    align=["left", "right"],
                ),
            ]
        )
    return lines


def _unavailable_section(summary: AgentOperationsSummary) -> list[str]:
    rows: list[list[str]] = [
        ["summary", f"`{name}`", reason] for name, reason in summary.unavailable.items()
    ]
    for section in _SECTIONS:
        value = getattr(summary, section)
        if isinstance(value, BaseModel):
            rows.extend(_explained_rows(section, value))
    if not rows:
        return []
    return [
        "",
        "## Unavailable Figures",
        "",
        render_markdown_table(["Section", "Figure", "Reason"], rows),
    ]


def _explained_rows(prefix: str, model: BaseModel) -> Iterable[list[str]]:
    unavailable = getattr(model, "unavailable", None)
    if isinstance(unavailable, Mapping):
        for name, reason in unavailable.items():
            yield [prefix, f"`{name}`", str(reason)]
    for name in type(model).model_fields:
        child = getattr(model, name)
        if name != "unavailable" and isinstance(child, BaseModel) and hasattr(child, "unavailable"):
            yield from _explained_rows(f"{prefix}.{name}", child)


# ── Formatting ────────────────────────────────────────────────────


def _distribution_table(rows: list[tuple[str, Distribution | None]], *, minutes: bool) -> str:
    fmt = _minutes if minutes else _seconds
    return render_markdown_table(
        ["Measure", "Count", "p50", "p90", "Max", "Mean"],
        [
            [
                label,
                str(dist.count) if dist else NOT_AVAILABLE,
                fmt(dist.p50 if dist else None),
                fmt(dist.p90 if dist else None),
                fmt(dist.max if dist else None),
                fmt(dist.mean if dist else None),
            ]
            for label, dist in rows
        ],
        align=["left", "right", "right", "right", "right", "right"],
    )


def _slowest_row(chain: ItemChain) -> list[str]:
    slowest = chain.slowest_step
    step = (
        f"`{slowest.stage}` / `{slowest.step_id}` ({_minutes(slowest.elapsed_s)})"
        if slowest
        else NOT_AVAILABLE
    )
    return [
        f"`{chain.item_key}`",
        _minutes(chain.chain_running_s),
        _minutes(chain.barrier_wait_s),
        _minutes(chain.chain_span_s),
        step,
    ]


def _missing_section(summary: AgentOperationsSummary, name: str) -> str:
    return f"Unavailable: {summary.unavailable.get(name, 'not recorded')}."


def _text(value: str | None) -> str:
    return value or NOT_AVAILABLE


def _count(value: int | None) -> str:
    return f"{value:,}" if value is not None else NOT_AVAILABLE


def _number(value: float | None, digits: int, *, suffix: str = "") -> str:
    return f"{value:,.{digits}f}{suffix}" if value is not None else NOT_AVAILABLE


def _seconds(value: float | None, *, digits: int = 1) -> str:
    return f"{value:,.{digits}f} s" if value is not None else NOT_AVAILABLE


def _minutes(value: float | None) -> str:
    return f"{value / 60:,.1f} min" if value is not None else NOT_AVAILABLE


def _pct(value: float | None) -> str:
    return f"{value * 100:.1f}%" if value is not None else NOT_AVAILABLE


def _money(value: float | None) -> str:
    return f"USD {value:,.2f}" if value is not None else NOT_AVAILABLE


def _list_cost(value: float | None, *, lower_bound: bool) -> str:
    """Mark a list cost that leaves out unpriced invocations as the lower bound it is."""
    return f"at least {_money(value)}" if lower_bound and value is not None else _money(value)


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count:,} {singular if count == 1 else plural}"


def _pool_samples(pool: PoolRow) -> str:
    if pool.sample_source == "pressure_check":
        return f"{pool.pressure_checks} pressure checks"
    return str(pool.health_samples)


def _gib(value: int | None) -> str:
    return f"{value / 1024**3:.2f} GiB" if value is not None else NOT_AVAILABLE


def _mib(value: int | None) -> str:
    return f"{value / 1024**2:,.1f} MiB" if value is not None else NOT_AVAILABLE


def _gb(value: float | None) -> str:
    return f"{value:.2f} GB" if value is not None else NOT_AVAILABLE


def _counts(values: Mapping[str, int] | None) -> str:
    if values is None:
        return NOT_AVAILABLE
    if not values:
        return "none"
    return ", ".join(f"{key} {count:,}" for key, count in values.items())
