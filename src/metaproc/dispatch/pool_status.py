"""At-a-glance pool status report (plan-2026-04-21 P4.1).

Pure composition over :class:`~metaproc.dispatch.credential_pool.PoolBackend`
state. No I/O beyond the backend's ``list_entries`` call; no provider
probing (that's P4.2 ``auth preflight``). Designed to answer the
single question "across my N Claude and Codex accounts, which are
usable right now?" in <50ms.

The pure-function design means the renderer is trivially testable
(feed it a pool with known states, assert on the report) and
reusable from anywhere — Typer command, future web UI, agent
introspection, etc.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from metaproc.dispatch.credential_pool import (
    EntryState,
    PoolEntry,
    eligible_labels,
)

if TYPE_CHECKING:
    from metaproc.dispatch.credential_pool import PoolBackend


@dataclass(frozen=True)
class LabelReport:
    """Per-label view surfaced in the at-a-glance status.

    Stays intentionally narrow: the things an operator needs to answer
    "is this credential usable right now?". Additions (severity,
    known_bug_signature, quota remaining) layer on top in later
    phases without breaking the schema.

    ``eligible`` is derived from :func:`eligible_labels` — cheaper to
    compute once and pass through than have every renderer re-derive
    it. ``last_ok_age_s`` and ``cooling_remaining_s`` are computed at
    report time relative to ``now`` so renderers don't need access to
    a clock.
    """

    adapter: str
    label: str
    status: str
    fp: str
    eligible: bool
    last_ok_ts: int | None
    last_ok_age_s: int | None
    last_quota_ts: int | None
    cooling_until_ts: int | None
    cooling_remaining_s: int | None
    # Schema_version 2 (Vehicle A pool redesign, 2026-04-28). vehicle is
    # the StrEnum value; quota_group is rendered "org:abc12345…" /
    # "account:def67890…" / "unknown" by the calling renderer. Operators
    # use these to confirm a label is on the recommended primary
    # (Vehicle A) and to audit which labels share a quota group.
    vehicle: str = "login_credentials"
    account_id: str | None = None
    organization_uuid: str | None = None
    quota_group_kind: str = "unknown"
    quota_group_value: str = ""


@dataclass(frozen=True)
class AdapterReport:
    """Per-adapter rollup: eligible/total + earliest reset + labels."""

    adapter: str
    eligible_count: int
    total_count: int
    earliest_reset_ts: int | None
    earliest_reset_remaining_s: int | None
    labels: list[LabelReport] = field(default_factory=list)


@dataclass(frozen=True)
class PoolStatusReport:
    """Top-level status snapshot for a pool user."""

    pool_user: str
    backend: str
    generated_at_ts: int
    adapters: list[AdapterReport] = field(default_factory=list)


# ── Composition ────────────────────────────────────────────────


def _label_from_entry(entry: PoolEntry, *, now: int) -> LabelReport:
    """Project a :class:`PoolEntry` into a :class:`LabelReport`."""
    state: EntryState = entry.state
    last_ok_age = max(0, now - state.last_ok_ts) if state.last_ok_ts is not None else None
    cooling_remaining: int | None = None
    if state.cooling_until_ts is not None:
        cooling_remaining = max(0, state.cooling_until_ts - now)
    # Eligibility = what the coordinator's selector would pick. Active
    # entries + past-cooling entries, matching eligible_labels().
    is_eligible = bool(eligible_labels([entry], now=now))
    return LabelReport(
        adapter=entry.adapter,
        label=entry.label,
        status=state.status,
        fp=state.fp,
        eligible=is_eligible,
        last_ok_ts=state.last_ok_ts,
        last_ok_age_s=last_ok_age,
        last_quota_ts=state.last_quota_ts,
        cooling_until_ts=state.cooling_until_ts,
        cooling_remaining_s=cooling_remaining,
        vehicle=state.vehicle.value,
        account_id=state.account_id,
        organization_uuid=state.organization_uuid,
        quota_group_kind=state.quota_group.kind,
        quota_group_value=state.quota_group.value,
    )


def build_pool_status(
    backend: PoolBackend,
    *,
    pool_user: str,
    backend_name: str,
    adapter: str | None = None,
    now: int | None = None,
) -> PoolStatusReport:
    """Return a snapshot of pool state suitable for an operator dashboard.

    Runs a single ``list_entries`` call against the backend (optionally
    filtered by *adapter*) and composes per-adapter rollups. When the
    pool has no entries for the filter, returns a report with an empty
    ``adapters`` list — callers render "no labels configured" rather
    than erroring.

    The ``earliest_reset_ts`` within an :class:`AdapterReport` is the
    minimum ``cooling_until_ts`` across that adapter's cooling labels —
    the single most useful "when can I dispatch?" number when labels
    are throttled. ``None`` when no labels are cooling.
    """
    current = now if now is not None else int(time.time())
    entries = backend.list_entries(adapter=adapter)
    # Group by adapter, preserving discovery order for stable output.
    by_adapter: dict[str, list[PoolEntry]] = {}
    for entry in entries:
        by_adapter.setdefault(entry.adapter, []).append(entry)

    adapter_reports: list[AdapterReport] = []
    for adapter_name in sorted(by_adapter):
        pool_entries = by_adapter[adapter_name]
        labels = [_label_from_entry(e, now=current) for e in pool_entries]
        # Stable label ordering: eligible first, then cooling, then
        # expired/disabled. Within each group, ascending by label name.
        labels.sort(key=lambda lr: (not lr.eligible, lr.status, lr.label))
        eligible_count = sum(1 for lr in labels if lr.eligible)
        earliest_reset = min(
            (
                lr.cooling_until_ts
                for lr in labels
                if lr.cooling_until_ts is not None and lr.status == "cooling"
            ),
            default=None,
        )
        earliest_remaining = (
            max(0, earliest_reset - current) if earliest_reset is not None else None
        )
        adapter_reports.append(
            AdapterReport(
                adapter=adapter_name,
                eligible_count=eligible_count,
                total_count=len(labels),
                earliest_reset_ts=earliest_reset,
                earliest_reset_remaining_s=earliest_remaining,
                labels=labels,
            )
        )

    return PoolStatusReport(
        pool_user=pool_user,
        backend=backend_name,
        generated_at_ts=current,
        adapters=adapter_reports,
    )


# ── Rendering helpers ──────────────────────────────────────────


def _fmt_relative(seconds: int | None, *, past: bool = False) -> str:
    """Format a duration in seconds as a compact relative-time string.

    ``past=True`` → "2m ago", "1h ago", "—" for None.
    ``past=False`` → "in 2m", "in 1h 29m".
    """
    if seconds is None:
        return "—"
    if seconds < 60:
        base = f"{seconds}s"
    elif seconds < 3600:
        base = f"{seconds // 60}m"
    elif seconds < 86400:
        hours = seconds // 3600
        mins = (seconds % 3600) // 60
        base = f"{hours}h" if mins == 0 else f"{hours}h {mins}m"
    else:
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        base = f"{days}d" if hours == 0 else f"{days}d {hours}h"
    return f"{base} ago" if past else f"in {base}"


def _fmt_abs_ts(ts: int | None) -> str:
    """UTC absolute timestamp for machine-readable output."""
    if ts is None:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts))


def render_text(report: PoolStatusReport, *, use_color: bool = False) -> str:
    """Compact human-readable rendering.

    Shape (per adapter):

        CLAUDE-CODE-CLI (2/2 eligible)
          laptop   active   last_ok 2m ago
          home     active   last_ok 47m ago
          earliest_reset: —

    Coloring (when ``use_color=True`` and a TTY) uses ANSI:
    green=active, yellow=cooling, red=expired, dim=disabled.
    """
    lines: list[str] = []
    header = f"POOL USER: {report.pool_user}  BACKEND: {report.backend}"
    lines.append(header)
    if not report.adapters:
        lines.append("(no pool labels configured)")
        return "\n".join(lines)

    for adapter_report in report.adapters:
        lines.append("")
        lines.append(
            f"{adapter_report.adapter.upper()} "
            f"({adapter_report.eligible_count}/{adapter_report.total_count} eligible)"
        )
        for lr in adapter_report.labels:
            status_tag = _colorize_status(lr.status, use_color=use_color)
            if lr.status == "active":
                detail = f"last_ok {_fmt_relative(lr.last_ok_age_s, past=True)}"
            elif lr.status == "cooling":
                abs_ts = _fmt_abs_ts(lr.cooling_until_ts)
                rel = _fmt_relative(lr.cooling_remaining_s)
                detail = f"until {abs_ts} ({rel})"
            elif lr.status == "expired":
                detail = "run `auth push` to rotate"
            else:  # disabled
                detail = "re-enable via `auth enable`"
            # Vehicle tag — surface "A" for OAUTH_TOKEN (recommended
            # primary) so an operator scanning a pool of mixed labels
            # can spot legacy Vehicle B entries at a glance.
            vehicle_tag = "A" if lr.vehicle == "claude_code_oauth_token" else "B"
            lines.append(f"  {lr.label:<12s} [{vehicle_tag}] {status_tag:<24s} {detail}")
        reset_line = (
            f"  earliest_reset: {_fmt_abs_ts(adapter_report.earliest_reset_ts)} "
            f"({_fmt_relative(adapter_report.earliest_reset_remaining_s)})"
            if adapter_report.earliest_reset_ts is not None
            else "  earliest_reset: —"
        )
        lines.append(reset_line)

    return "\n".join(lines)


_ANSI_GREEN = "\x1b[32m"
_ANSI_YELLOW = "\x1b[33m"
_ANSI_RED = "\x1b[31m"
_ANSI_DIM = "\x1b[2m"
_ANSI_RESET = "\x1b[0m"


def _colorize_status(status: str, *, use_color: bool) -> str:
    """Return a color-tagged status label when use_color is True."""
    label = status.upper()
    if not use_color:
        return label
    if status == "active":
        color = _ANSI_GREEN
    elif status == "cooling":
        color = _ANSI_YELLOW
    elif status == "expired":
        color = _ANSI_RED
    else:  # disabled
        color = _ANSI_DIM
    return f"{color}{label}{_ANSI_RESET}"


def to_json_dict(report: PoolStatusReport) -> dict[str, object]:
    """Serialize a report for ``--json`` output.

    Mirrors the dataclass field names so the schema is stable for
    automation / agent callers. Includes absolute timestamps so
    downstream tooling can render its own relative times.
    """
    return {
        "pool_user": report.pool_user,
        "backend": report.backend,
        "generated_at_ts": report.generated_at_ts,
        "adapters": [
            {
                "adapter": adapter_report.adapter,
                "eligible_count": adapter_report.eligible_count,
                "total_count": adapter_report.total_count,
                "earliest_reset_ts": adapter_report.earliest_reset_ts,
                "earliest_reset_remaining_s": adapter_report.earliest_reset_remaining_s,
                "labels": [
                    {
                        "label": lr.label,
                        "status": lr.status,
                        "fp": lr.fp,
                        "eligible": lr.eligible,
                        "last_ok_ts": lr.last_ok_ts,
                        "last_ok_age_s": lr.last_ok_age_s,
                        "last_quota_ts": lr.last_quota_ts,
                        "cooling_until_ts": lr.cooling_until_ts,
                        "cooling_remaining_s": lr.cooling_remaining_s,
                        "vehicle": lr.vehicle,
                        "account_id": lr.account_id,
                        "organization_uuid": lr.organization_uuid,
                        "quota_group_kind": lr.quota_group_kind,
                        "quota_group_value": lr.quota_group_value,
                    }
                    for lr in adapter_report.labels
                ],
            }
            for adapter_report in report.adapters
        ],
    }


__all__ = [
    "AdapterReport",
    "LabelReport",
    "PoolStatusReport",
    "build_pool_status",
    "render_text",
    "to_json_dict",
]
