"""Pre-flight quota gate for auth-bound dispatch steps.

plan-2026-04-21-auth-credential-pool.md §Components 8 (P2c.1).

Refuses to launch into a pool that's already too empty for the
projected demand. The single highest-leverage reliability fix in
Phase 2c — prevents the 2026-04-23 failure mode where a dispatch
crashed at T+30 min after burning 30 workers into a 9%-remaining
quota bucket.

Gate runs twice per dispatch:
1. **Aggregate** at dispatch start — sums projected demand across
   all auth-bound steps in the DAG against the pool-wide headroom.
2. **Per-step** immediately before each fan-out — re-checks with
   live quota (upstream may have burned quota; pool may have gained
   labels mid-run).

Three postures via ``--auth-preflight-quota-guard``:
- ``off``: skip the check entirely.
- ``warn``: compute verdict, log ``quota_warn`` event on near-empty
  pool, always return ``go``.
- ``refuse``: near-empty → return ``refuse`` so the dispatch command
  stops before launching work.

The gate is intentionally code-only; no LLM judgment in the
critical path (G15).

The companion dispatch-start ambient-auth environment sweep is
:func:`check_dispatch_auth_env` — separate from the
quota check because it consults ``os.environ`` rather than pool
telemetry, but shares the :data:`GuardPosture` semantics so the
operator-facing flag set stays small.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path as _Path
from typing import TYPE_CHECKING, Literal

from metaproc.adapters.registry import get_auth_capable
from metaproc.dispatch.credential_pool import eligible_labels
from metaproc.dispatch.pool_dispatch import auth_override_refusal_keys

if TYPE_CHECKING:
    from metaproc.adapters.base import AuthCapableCliAdapter
    from metaproc.dispatch.credential_pool import PoolBackend
    from metaproc.dispatch.slot_coordinator import SlotCoordinator

log = logging.getLogger(__name__)


# Fallback constant — ~ two full tool-heavy claude-code-cli
# invocations per item. Revisit after telemetry lands.
CLAUDE_CODE_CLI_DEFAULT_TOKENS_PER_ITEM: int = 220_000


GuardPosture = Literal["off", "warn", "refuse"]

_VALID_GUARD_POSTURES: tuple[GuardPosture, ...] = ("off", "warn", "refuse")


def validate_guard_posture(value: str) -> GuardPosture:
    """Coerce *value* to a :data:`GuardPosture` or raise ``ValueError``.

    Used by the ``--auth-preflight-quota-guard`` CLI plumbing on
    ``run-process`` and ``run-parallel`` to fail fast at parse time
    rather than have an unrecognized posture silently weaken or
    strengthen the gate based on telemetry availability (upstream change
    review P2).
    """
    if value in _VALID_GUARD_POSTURES:
        return value  # type: ignore[return-value]  # narrowed by membership
    msg = (
        f"--auth-preflight-quota-guard must be one of "
        f"{'|'.join(_VALID_GUARD_POSTURES)}, got {value!r}"
    )
    raise ValueError(msg)


@dataclass(frozen=True)
class QuotaHeadroom:
    """Summary of what the pool can serve right now.

    Two complementary views of remaining capacity, both populated when
    the adapter supports them:

    - **Token-unit view** (``pool_remaining_units`` /
      ``pool_total_units``): the sum of ``remaining_units`` /
      ``total_units`` across eligible labels for the given adapter.
      ``None`` means at least one eligible adapter call returned no
      unit data — caller should treat this as "unknown" rather than
      "zero".

    - **Utilization-ratio view** (``min_remaining_ratio``): the
      minimum ``remaining_ratio`` across eligible labels that
      reported one. Encodes "the binding window across the pool" —
      e.g. a single alt at 100% 5h utilization drives the aggregate
      ratio to 0 even when other alts are fresh, because the
      binding-window-of-the-binding-label gates an acquisition
      against that label. ``None`` means no eligible adapter call
      returned a ratio.

    The two views coexist because Anthropic's Claude Code surface
    publishes per-window utilization (0.0–1.0) but not absolute
    token caps, while other adapters (Codex refresh probes, hopefully
    a future Anthropic native quota endpoint) can expose absolute
    units. ``check_step_preflight`` decides on whichever signal is
    available; both being ``None`` means "unknown".

    ``earliest_reset_ts`` is the minimum ``cooling_until_ts`` across
    cooling labels — used by wait-mode to decide how long to sleep.
    """

    adapter: str
    pool_remaining_units: int | None
    pool_total_units: int | None
    earliest_reset_ts: int | None
    eligible_label_count: int
    # Phase 3: Anthropic publishes per-window
    # utilization not token caps; this gives the gate a signal it
    # can actually refuse on when token-unit data is unavailable.
    min_remaining_ratio: float | None = None


@dataclass(frozen=True)
class PreflightVerdict:
    """Outcome of a single preflight check.

    ``status``: ``go`` (dispatch proceeds), ``warn`` (dispatch
    proceeds but emits a quota_warn event), or ``refuse`` (caller
    stops before launch).

    ``projected_units`` is the estimate the gate used; ``None`` when
    the dispatcher couldn't estimate (no telemetry and no adapter
    constant).

    ``message`` is operator-facing: explains the shortfall and
    names remediation options.
    """

    status: Literal["go", "warn", "refuse"]
    projected_units: int | None
    headroom: QuotaHeadroom
    message: str


# Threshold at which "warn" mode emits a quota_warn event and
# "refuse" mode refuses the launch. 0.8 = "projected > 80% of
# current headroom", i.e. no safety margin beyond the 20% retry
# overhead the projector already bakes in. Tunable later.
_REFUSE_RATIO: float = 0.8

# Ratio-based gate threshold (Phase 3). When token
# units are unavailable but per-window utilization is, the gate
# refuses if the binding window has < 20% remaining (== the same
# 80% utilization risk the unit-based path bakes in). Mirrors
# _REFUSE_RATIO so behavior is consistent across signal kinds.
_REFUSE_REMAINING_RATIO: float = 0.2


def summarize_headroom(
    backend: PoolBackend,
    adapter: str,
    *,
    coordinator: SlotCoordinator | None = None,
    adapter_impl: AuthCapableCliAdapter | None = None,
    now: int | None = None,
) -> QuotaHeadroom:
    """Compute aggregate quota headroom for *adapter* across its labels.

    Walks the pool's entries for ``adapter``, calls
    ``adapter_impl.query_quota_usage`` for each (if the adapter
    implements it), and sums remaining + total units.

    When ``query_quota_usage`` returns ``None`` for any label we
    treat the aggregate as unknown and return
    ``pool_remaining_units=None`` — the refuse-posture caller falls
    back to a per-adapter constant rather than assuming zero.

    ``coordinator`` / ``adapter_impl`` are injection seams so tests
    can pass a stub without threading a full registry.
    """

    del coordinator  # reserved for future active-slot awareness

    impl = adapter_impl
    if impl is None:
        impl = get_auth_capable(adapter)

    entries = backend.list_entries(adapter=adapter)
    eligible = eligible_labels(entries, now=now)
    earliest_reset: int | None = None
    remaining_total: int = 0
    total_total: int = 0
    any_unknown = False
    any_unit_data = False
    # Track the binding (worst-case) remaining_ratio independently
    # of unit accounting: an adapter that publishes ratio without
    # absolute units (Anthropic's per-window utilization is the
    # canonical case) still wants its signal to reach the gate.
    min_ratio: float | None = None
    for entry in entries:
        ts = entry.state.cooling_until_ts
        if ts is not None and (earliest_reset is None or ts < earliest_reset):
            earliest_reset = ts
    if impl is None:
        # Adapter not auth-capable or not registered — can't estimate.
        return QuotaHeadroom(
            adapter=adapter,
            pool_remaining_units=None,
            pool_total_units=None,
            earliest_reset_ts=earliest_reset,
            eligible_label_count=len(eligible),
            min_remaining_ratio=None,
        )

    for entry in eligible:
        # query_quota_usage requires a Path per the adapter protocol.
        # For aggregation we conservatively skip materialization
        # (spinning up a transient slot per label is expensive at
        # dispatch start) and pass a sentinel placeholder Path. Adapters
        # that can cheaply estimate without an actual slot (Codex
        # refresh probe, Claude's future quota endpoint) return usage;
        # adapters that require a real slot return None, which we
        # interpret as "unknown" at the aggregate level.
        try:
            usage = impl.query_quota_usage(_Path(f"/nonexistent/{entry.label}"))
        except Exception:  # noqa: BLE001 — best-effort summary
            usage = None
        if usage is None:
            any_unknown = True
            continue
        if usage.remaining_units is not None:
            remaining_total += usage.remaining_units
            any_unit_data = True
        if usage.total_units is not None:
            total_total += usage.total_units
            any_unit_data = True
        # Ratio merge: keep the minimum across labels — the binding
        # window on the binding label is what the next acquisition
        # actually has to satisfy. A single full label drags the
        # aggregate ratio down without affecting unit aggregation.
        if usage.remaining_ratio is not None:
            ratio = max(0.0, min(1.0, float(usage.remaining_ratio)))
            if min_ratio is None or ratio < min_ratio:
                min_ratio = ratio
    # Units stay "unknown" when no eligible label produced unit data
    # (rather than degrading to 0, which would falsely refuse every
    # ratio-only adapter under posture=refuse).
    units_known = any_unit_data and not any_unknown and bool(eligible)
    remaining = remaining_total if units_known else None
    total = (total_total or None) if units_known else None
    return QuotaHeadroom(
        adapter=adapter,
        pool_remaining_units=remaining,
        pool_total_units=total,
        earliest_reset_ts=earliest_reset,
        eligible_label_count=len(eligible),
        min_remaining_ratio=min_ratio,
    )


def _format_ts(ts: int | None) -> str:
    if ts is None:
        return "<unknown>"
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts))


def _compose_message(
    status: str,
    adapter: str,
    projected: int | None,
    headroom: QuotaHeadroom,
) -> str:
    lines = [
        f"preflight quota gate: adapter={adapter} status={status}",
        f"  eligible labels: {headroom.eligible_label_count}",
    ]
    if headroom.pool_remaining_units is not None:
        lines.append(f"  pool remaining: {headroom.pool_remaining_units:,} units")
    elif headroom.min_remaining_ratio is not None:
        # Ratio-based view: surface the binding window's headroom
        # so operators see what the gate actually decided on.
        pct_remaining = int(round(100 * headroom.min_remaining_ratio))
        lines.append(
            f"  pool remaining: {pct_remaining}% (binding window; "
            "token caps not published by adapter)"
        )
    else:
        lines.append("  pool remaining: <unknown>")
    if headroom.pool_total_units is not None:
        lines.append(f"  pool total: {headroom.pool_total_units:,} units")
    if projected is not None:
        lines.append(f"  projected demand: {projected:,} units")
    if headroom.earliest_reset_ts is not None:
        lines.append(f"  earliest cooling reset: {_format_ts(headroom.earliest_reset_ts)}")
    if status == "refuse":
        lines.append("  remediation options:")
        if headroom.earliest_reset_ts is not None:
            lines.append(
                f"    (a) wait until {_format_ts(headroom.earliest_reset_ts)} and re-dispatch"
            )
        lines.append("    (b) `metaproc auth push` additional pool labels")
        lines.append("    (c) reduce fan-out or split the run")
        lines.append("    (d) retry after the reported quota reset time")
    return "\n".join(lines)


def check_step_preflight(  # noqa: PLR0913
    backend: PoolBackend,
    *,
    adapter: str,
    step_id: str,
    fan_out_size: int,
    mean_cost_per_item: int | None = None,
    posture: GuardPosture = "warn",
    coordinator: SlotCoordinator | None = None,
    adapter_impl: AuthCapableCliAdapter | None = None,
    now: int | None = None,
) -> PreflightVerdict:
    """Return a :class:`PreflightVerdict` for a single adapter-bound step.

    *posture* = ``off`` short-circuits to ``go`` without any probe.
    *posture* = ``warn`` always returns ``go`` but sets status=warn
    in the message (caller may emit a quota_warn event).
    *posture* = ``refuse`` returns ``refuse`` when projected > 0.8 ×
    pool_remaining (plus 20% retry overhead in the projection).

    ``mean_cost_per_item`` defaults to the per-adapter constant
    when None — in Phase 2c we ship the constant; telemetry-driven
    learning from ``auth_outcome`` events is a follow-up.
    """
    del step_id  # reserved for telemetry-driven learning (P2c.1 follow-up)

    headroom = summarize_headroom(
        backend,
        adapter,
        coordinator=coordinator,
        adapter_impl=adapter_impl,
        now=now,
    )

    cost = mean_cost_per_item
    if cost is None and adapter == "claude-code-cli":
        cost = CLAUDE_CODE_CLI_DEFAULT_TOKENS_PER_ITEM
    projected: int | None
    if cost is None:
        projected = None
    else:
        # 20% retry overhead per spec Components §8.
        projected = int(fan_out_size * cost * 1.2)

    if posture == "off":
        return PreflightVerdict(
            status="go",
            projected_units=projected,
            headroom=headroom,
            message="preflight: off",
        )

    status: Literal["go", "warn", "refuse"] = "go"

    # Path 1: Token-unit signal available. Projected demand vs pool
    # remaining is the most precise check; prefer it when it exists.
    if headroom.pool_remaining_units is not None and projected is not None:
        if projected > _REFUSE_RATIO * headroom.pool_remaining_units:
            if posture == "warn":
                log.warning(
                    "preflight: projected %d > 80%% of pool remaining %d (adapter=%s)",
                    projected,
                    headroom.pool_remaining_units,
                    adapter,
                )
                status = "warn"
            else:  # refuse
                status = "refuse"
        return PreflightVerdict(
            status=status,
            projected_units=projected,
            headroom=headroom,
            message=_compose_message(status, adapter, projected, headroom),
        )

    # Path 2: Utilization-ratio signal available even without unit
    # data (Anthropic's per-window utilization is the canonical
    # case). Refuse when the binding window has < 20% remaining,
    # mirroring the 80%-of-headroom risk threshold the unit path
    # bakes in. This is the path that fires when
    # claude_code.query_quota_usage synthesizes
    # from rate_limit_event records.
    if headroom.min_remaining_ratio is not None:
        if headroom.min_remaining_ratio < _REFUSE_REMAINING_RATIO:
            if posture == "warn":
                log.warning(
                    "preflight: binding window remaining_ratio=%.2f < %.2f (adapter=%s)",
                    headroom.min_remaining_ratio,
                    _REFUSE_REMAINING_RATIO,
                    adapter,
                )
                status = "warn"
            else:  # refuse
                status = "refuse"
        return PreflightVerdict(
            status=status,
            projected_units=projected,
            headroom=headroom,
            message=_compose_message(status, adapter, projected, headroom),
        )

    # Path 3: Both signals unknown. We can't prove trouble, so go
    # under warn with an explicit incomplete-data note. Under refuse
    # the same case is also "go" because refusing on missing data
    # would break every run until telemetry lands.
    if posture == "warn":
        status = "warn"
        log.warning(
            "preflight: quota data incomplete (adapter=%s); defaulting to go",
            adapter,
        )
    return PreflightVerdict(
        status=status,
        projected_units=projected,
        headroom=headroom,
        message=_compose_message(status, adapter, projected, headroom),
    )


@dataclass(frozen=True)
class DispatchAuthEnvVerdict:
    """Outcome of the dispatch-start ambient-auth env sweep.

    ``status``: ``go`` (clean env or posture=off), ``warn`` (conflict
    found but posture=warn — caller logs and proceeds), ``refuse``
    (conflict found and posture=refuse — caller raises before any
    step runs).

    ``conflicting_keys`` names the env vars that, if left set, would
    override the pooled OAuth credential. Empty tuple means the env
    is clean.

    ``message`` is operator-facing and names the unset command.
    """

    status: Literal["go", "warn", "refuse"]
    conflicting_keys: tuple[str, ...]
    message: str


def check_dispatch_auth_env(
    adapter: str,
    *,
    posture: GuardPosture = "warn",
    env: Mapping[str, str] | None = None,
) -> DispatchAuthEnvVerdict:
    """Inspect ``os.environ`` for ambient auth vars that would override the pool.

    Mirrors the per-item ``compose_slot_env`` refusal
    (:func:`metaproc.dispatch.pool_dispatch.compose_slot_env`) but
    fires once at dispatch start so a non-pool step can't complete
    before the next step's first item discovers the conflict. For example,
    a long ``extract-items`` step may bypass the pool entirely while an
    ambient credential would conflict with the following pooled step.

    Posture semantics match :func:`check_step_preflight`:

    - ``off``: returns ``go`` without inspecting env.
    - ``warn``: returns ``warn`` with conflicting keys named when any
      ambient refusal-key is set; ``go`` otherwise. The per-item
      ``compose_slot_env`` check is the defense-in-depth backstop.
    - ``refuse``: returns ``refuse`` when any ambient refusal-key is
      set; ``go`` otherwise. Caller raises ``CLIError`` before step 0.

    *env* defaults to ``os.environ`` — injectable for tests.
    """
    if posture == "off":
        return DispatchAuthEnvVerdict(
            status="go",
            conflicting_keys=(),
            message="dispatch auth env: off",
        )

    env_map = os.environ if env is None else env
    refusal_keys = auth_override_refusal_keys(adapter)
    conflicts = tuple(k for k in refusal_keys if env_map.get(k))

    if not conflicts:
        return DispatchAuthEnvVerdict(
            status="go",
            conflicting_keys=(),
            message=f"dispatch auth env: clean (adapter={adapter})",
        )

    names = ", ".join(conflicts)
    unset_cmd = " ".join(f"unset {k}" for k in conflicts)
    base_msg = (
        f"ambient auth var(s) would override the pooled OAuth credential "
        f"for adapter={adapter}: {names}. Run `{unset_cmd}` (or relaunch "
        f"with `env -u {' -u '.join(conflicts)}`) before retrying."
    )

    if posture == "warn":
        log.warning("dispatch auth env: %s", base_msg)
        return DispatchAuthEnvVerdict(
            status="warn",
            conflicting_keys=conflicts,
            message=f"dispatch auth env: warn — {base_msg}",
        )

    # refuse
    return DispatchAuthEnvVerdict(
        status="refuse",
        conflicting_keys=conflicts,
        message=f"dispatch auth env: refuse — {base_msg}",
    )


__all__ = [
    "CLAUDE_CODE_CLI_DEFAULT_TOKENS_PER_ITEM",
    "DispatchAuthEnvVerdict",
    "GuardPosture",
    "PreflightVerdict",
    "QuotaHeadroom",
    "check_dispatch_auth_env",
    "check_step_preflight",
    "summarize_headroom",
    "validate_guard_posture",
]
