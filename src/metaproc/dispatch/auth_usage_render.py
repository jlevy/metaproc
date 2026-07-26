"""Text + JSON rendering for ``metaproc auth usage`` / ``auth doctor``.

Spec: ``docs/project/specs/active/plan-2026-05-03-auth-observability-and-load-balancing.md``
Bead: ``internal issue``.

Pure functions over the :class:`metaproc.dispatch.auth_usage.LabelUsage`
data the aggregator builds. The CLI layer (``metaproc auth …`` in
``commands/auth.py``) is a thin wrapper that calls the aggregator
then routes through these renderers — keeps formatting decisions
testable without a Typer harness.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from metaproc.dispatch.auth_usage import LabelUsage, RateLimitWindow, detect_inconsistencies

# ── Format helpers ─────────────────────────────────────────────


def _fmt_pct(util: float | None) -> str:
    if util is None:
        return "—"
    return f"{int(round(util * 100))}%"


def _fmt_resets(window: RateLimitWindow | None) -> str:
    if window is None or window.resets_at is None:
        return "—"
    dt = datetime.fromtimestamp(window.resets_at, tz=UTC)
    return dt.strftime("%H:%MZ")


def _fmt_dt(dt: datetime | None, *, fmt: str = "%H:%MZ") -> str:
    if dt is None:
        return "—"
    return dt.strftime(fmt)


def _fmt_window_summary(window: RateLimitWindow | None) -> str:
    """One-line summary of a rate-limit window for the deep-dive view."""
    if window is None:
        return "no data"
    util = _fmt_pct(window.utilization)
    return f"{window.status} (util={util}, resets {_fmt_resets(window)})"


# ── Text renderers ─────────────────────────────────────────────


def render_run_usage_text(
    *,
    run_dir: str,
    usage_by_label: dict[tuple[str, str], LabelUsage],
    config: dict[str, Any] | None = None,
) -> str:
    """Render the ``metaproc auth usage <run-dir>`` text view.

    *config* is the optional run-config snapshot — operators read it
    to confirm what auth flags / concurrency were in effect. Empty
    dict / None renders a "no config snapshot" line so the output is
    diagnostic even when run-config persistence (Phase 3 internal issue)
    hasn't shipped yet.
    """
    lines: list[str] = []
    lines.append(f"RUN: {run_dir}")
    if not usage_by_label:
        lines.append("  no auth-pool events recorded for this run")
        return "\n".join(lines)

    lines.append("")
    lines.append("PER-LABEL ATTRIBUTION")
    for (adapter, label), u in sorted(usage_by_label.items()):
        if u.invocations_total == 0:
            lines.append(f"  {label:10s}  0 invocations  — never used —")
            continue
        ok = u.invocations_ok
        failed = u.invocations_failed
        pct = (100 * failed / u.invocations_total) if u.invocations_total else 0
        lines.append(
            f"  {label:10s}  {u.invocations_total} invocations  "
            f"{ok} ok / {failed} failed ({pct:.0f}%)"
        )
        if u.known_bugs:
            bugs = ", ".join(f"{name} × {n}" for name, n in sorted(u.known_bugs.items()))
            lines.append(f"             known-bugs: {bugs}")
        if u.last_used_ts is not None:
            lines.append(f"             last seen: {_fmt_dt(u.last_used_ts)}")
        del adapter  # informational; included in dict key, not rendered

    lines.append("")
    lines.append("LATEST RATE-LIMIT EVENTS PER LABEL")
    for (_a, label), u in sorted(usage_by_label.items()):
        if u.five_hour is None and u.seven_day is None:
            lines.append(f"  {label:10s}  no rate-limit data — label was not invoked")
            continue
        five = _fmt_window_summary(u.five_hour)
        seven = _fmt_window_summary(u.seven_day)
        lines.append(f"  {label:10s}  5h: {five} | 7d: {seven}")

    # Load-balancing check — borrows the inconsistency rule directly.
    lb_warnings: list[str] = []
    for (_a, label), u in sorted(usage_by_label.items()):
        if u.invocations_total == 0 and u.pool_state == "active":
            lb_warnings.append(f"label {label} active but 0 invocations — verify load-balancing")
    if lb_warnings:
        lines.append("")
        lines.append("LOAD-BALANCING CHECK")
        for warn in lb_warnings:
            lines.append(f"  ⚠ {warn}")

    if config:
        lines.append("")
        lines.append("CONFIG SNAPSHOT (from .state/run-config.yaml)")
        for k, v in sorted(config.items()):
            lines.append(f"  {k}: {v}")

    return "\n".join(lines)


def render_doctor_text(
    *,
    usage_by_label: dict[tuple[str, str], LabelUsage],
    probe_requested: bool = False,
) -> str:
    """Render the ``metaproc auth doctor`` text view.

    Per spec § doctor: never auto-probes. When *probe_requested* is
    False (the default), the output explicitly tells the operator how
    to opt in to a probe for the labels that would benefit. When
    True, the caller is expected to have populated ``LabelUsage.
    probe_state`` for each label (probe wiring lands separately).
    """
    issues_per_label: dict[tuple[str, str], list[str]] = {
        key: detect_inconsistencies(u) for key, u in usage_by_label.items()
    }
    total_issues = sum(len(v) for v in issues_per_label.values())

    lines: list[str] = []
    lines.append("metaproc auth doctor")
    lines.append(
        f"[1/3] pool state files .................. OK ({len(usage_by_label)} labels found)"
    )
    sessions_seen = sum(
        1 for u in usage_by_label.values() if u.last_event_ts is not None or u.invocations_total > 0
    )
    lines.append(
        f"[2/3] runpool-events + session JSONLs .... {sessions_seen} labels with recent activity"
    )
    lines.append(
        f"[3/3] cross-source consistency ........... "
        f"{total_issues} issue{'s' if total_issues != 1 else ''}"
    )
    if not probe_requested:
        lines.append(
            "[*]   live probes (--probe) .............. SKIPPED (default; pass --probe to run them)"
        )

    if total_issues == 0:
        lines.append("")
        lines.append("ISSUES")
        lines.append("  ✓ no inconsistencies detected")
        return "\n".join(lines)

    lines.append("")
    lines.append("ISSUES")
    for (adapter, label), issues in sorted(issues_per_label.items()):
        if not issues:
            continue
        for issue in issues:
            lines.append(f"  ⚠ {adapter}/{label}: {issue}")

    if not probe_requested:
        lines.append("")
        lines.append(
            "HINT: re-run with --probe to verify the inconsistent labels via live API call"
        )
    return "\n".join(lines)


# ── JSON / YAML serialization ──────────────────────────────────


def label_usage_to_dict(u: LabelUsage) -> dict[str, Any]:
    """Convert :class:`LabelUsage` to a plain dict for JSON / YAML.

    Datetimes serialize as ISO-8601 UTC strings; ``None`` fields stay
    ``None`` (not omitted) so consumers can rely on a fixed schema.
    """

    def _dt(d: datetime | None) -> str | None:
        return d.isoformat() if d is not None else None

    def _win(w: RateLimitWindow | None) -> dict[str, Any] | None:
        if w is None:
            return None
        return {
            "status": w.status,
            "utilization": w.utilization,
            "resets_at": w.resets_at,
        }

    return {
        "adapter": u.adapter,
        "label": u.label,
        "vehicle": u.vehicle,
        "fingerprint": u.fingerprint,
        "pool_state": u.pool_state,
        "cooling_until": _dt(u.cooling_until),
        "last_ok_ts": _dt(u.last_ok_ts),
        "last_quota_ts": _dt(u.last_quota_ts),
        "one_hour": _win(u.one_hour),
        "five_hour": _win(u.five_hour),
        "seven_day": _win(u.seven_day),
        "last_event_ts": _dt(u.last_event_ts),
        "invocations": {
            "total": u.invocations_total,
            "ok": u.invocations_ok,
            "failed": u.invocations_failed,
        },
        "known_bugs": dict(u.known_bugs),
        "last_used_ts": _dt(u.last_used_ts),
        "last_run_id": u.last_run_id,
        "probe": {
            "state": u.probe_state,
            "request_id": u.probe_request_id,
            "age_s": u.probe_age_s,
        },
        "inconsistencies": list(u.inconsistencies),
    }


def usage_report_to_dict(
    *,
    run_dir: str,
    usage_by_label: dict[tuple[str, str], LabelUsage],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "run_dir": run_dir,
        "labels": [label_usage_to_dict(u) for _, u in sorted(usage_by_label.items())],
        "config": dict(config or {}),
    }


def doctor_report_to_dict(
    *,
    usage_by_label: dict[tuple[str, str], LabelUsage],
    probe_requested: bool = False,
) -> dict[str, Any]:
    issues_per_label: dict[str, list[str]] = {}
    for (adapter, label), u in sorted(usage_by_label.items()):
        issues = detect_inconsistencies(u)
        if issues:
            issues_per_label[f"{adapter}/{label}"] = issues
    return {
        "labels_total": len(usage_by_label),
        "issues_total": sum(len(v) for v in issues_per_label.values()),
        "probe_requested": probe_requested,
        "issues_by_label": issues_per_label,
    }


__all__ = [
    "doctor_report_to_dict",
    "label_usage_to_dict",
    "render_doctor_text",
    "render_run_usage_text",
    "usage_report_to_dict",
]
