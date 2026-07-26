"""Pool dispatch integration for run_parallel.

Thin integration layer that wires :mod:`metaproc.dispatch.slot_coordinator`
into :mod:`metaproc.commands.run_parallel` via a single opt-in
config object. Non-pool runs pass ``pool_dispatch=None`` and see
zero behavior change; pool runs pass a populated
:class:`PoolDispatchConfig` and ``_build_prepare_launch`` leases a
slot per item, ``_process_completion`` tears down.

Kept as a distinct module (rather than inline in run_parallel)
because the pool wiring is orthogonal to the rest of the pool-free
dispatch — a future extraction of run_parallel's scheduler would
not touch this file, and the test surface stays clean.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import time as _time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from metaproc.adapters.base import AuthFailureClassification
from metaproc.adapters.claude_code import parse_claude_api_signals
from metaproc.adapters.registry import get_auth_capable
from metaproc.dispatch.credential_pool import (
    FallbackPolicy,
    PoolBackend,
    SelectionStrategy,
    Vehicle,
    fingerprint_blob,
    safe_apply_state,
    state_mark_cooling,
    state_mark_expired,
    state_mark_ok,
)
from metaproc.dispatch.slot_coordinator import (
    SlotCoordinator,
    SlotLease,
    apply_slot_env,
)

if TYPE_CHECKING:
    from metaproc.adapters.base import AuthFailureClassification
    from metaproc.adapters.claude_code import ClaudeApiSignals

log = logging.getLogger(__name__)


class PoolSlotUnavailableError(RuntimeError):
    """Raised when a pool dispatch can't acquire an eligible slot.

    Wraps a single classifier-reason string plus the (adapter, labels
    tried) tuple for the caller's fail-fast / wait / signal decision.
    Phase 2c's RetryLaterPolicy catches this at the dispatch layer
    and decides whether to block, exit 78, or raise up to the user.
    """

    def __init__(
        self, adapter: str, *, policy: FallbackPolicy, excluded: tuple[tuple[str, str], ...]
    ) -> None:
        self.adapter = adapter
        self.policy = policy
        self.excluded = excluded
        msg = (
            f"no eligible pool label for adapter={adapter!r} under policy={policy} "
            f"(excluded={list(excluded)})"
        )
        super().__init__(msg)


class PoolAuthOverrideError(RuntimeError):
    """Raised when ambient auth would override a leased pool slot."""


@dataclass(frozen=True)
class PoolDispatchConfig:
    """Per-dispatch pool configuration passed to :mod:`run_parallel`.

    ``strategy`` controls which label each item gets (see
    :class:`SelectionStrategy`). ``fallback_policy`` is consulted only
    when the strategy exhausts its labels — it walks compatible
    adapters under the orthogonal :class:`FallbackPolicy` enum.
    ``exclude`` covers operator-level pin-outs; per-item retry history
    is added at ``acquire_slot`` time via ``item_exclude``.
    """

    coordinator: SlotCoordinator
    adapter: str
    runs_dir: Path
    run_id: str
    step: str
    strategy: SelectionStrategy = SelectionStrategy()
    fallback_policy: FallbackPolicy = FallbackPolicy.NONE
    exclude: tuple[tuple[str, str], ...] = ()
    # When True (default), a 429 cooling failure on a label expands
    # pool_exclude to every sibling label sharing the failing label's
    # quota_group — Anthropic rate-limits at account/org level, so
    # walking past the whole group avoids back-to-back 429s.
    # Operators opt out (``--no-auth-cross-quota-group``) for
    # diagnostic dispatches against a single account where the
    # expansion would empty the pool.
    cross_quota_group: bool = True


def acquire_slot(
    config: PoolDispatchConfig,
    *,
    item: str,
    attempt: int,
    item_exclude: tuple[tuple[str, str], ...] = (),
    session_log_path: Path | None = None,
) -> SlotLease:
    """Acquire a pool slot for ``(adapter, item, attempt)``.

    Raises :class:`PoolSlotUnavailableError` when no eligible label
    exists under *config.strategy* (or the fallback walk).

    ``session_log_path`` flows through to the returned :class:`SlotLease`
    so the schema-v2 ``auth_lease_acquired`` and ``auth_outcome`` events
    can carry it as a join key.
    """
    effective_exclude = tuple({*config.exclude, *item_exclude})
    lease = config.coordinator.acquire_slot(
        config.adapter,
        runs_dir=config.runs_dir,
        run_id=config.run_id,
        step=config.step,
        item=item,
        attempt=attempt,
        strategy=config.strategy,
        exclude=effective_exclude,
        fallback_policy=config.fallback_policy,
        session_log_path=session_log_path,
    )
    if lease is None:
        raise PoolSlotUnavailableError(
            config.adapter, policy=config.fallback_policy, excluded=effective_exclude
        )
    return lease


def classify_failure_for_slot(
    lease: SlotLease,
    *,
    error_str: str,
    session_log_path: Path | None = None,
) -> AuthFailureClassification:
    """Map an error to an ``AuthFailureClassification`` via the lease's adapter.

    Looks up the adapter through :func:`get_auth_capable` (the
    coordinator's registry path already validated auth-capable-ness
    at lease time; this resolution is a second lookup so the teardown
    path doesn't need the coordinator handle).

    Reads slot-local ``claude-code-debug.log`` when it exists and prepends
    its content to ``error_str`` so the adapter's
    ``classify_failure`` sees the OAuth refresh boundaries + per-attempt
    API errors that would otherwise be lost on subprocess stderr.

    Returns ``AuthFailureClassification(status="unknown")`` when the
    adapter is somehow no longer resolvable — the generic retry path
    then wins and no pool state change is made.
    """

    # Content-validation failures aren't credential failures. Short-circuit before
    # invoking the adapter classifier, which would otherwise scan the validation
    # error (and the appended debug log) for transient-network tokens like
    # "timeout" / "stalled" / "5xx" and return ``transient-network-or-5xx`` with
    # RETRY_NOW. That misclassification was correct mechanically but wrong
    # semantically — the engine's own ``classify_error`` (engine/retry.py) already
    # owns the right retry decision for ``output validation failed`` (schema/
    # envelope/mismatch → FAIL; otherwise → RETRY). Returning ``unknown`` with
    # ``reason="invalid_outputs"`` here lets the engine's decision win without
    # the misleading network-failure label in the auth_outcome event stream.
    # See internal issue (2026-05-25 Wave 2 SNOW retry loop, ~20 min wasted on
    # 12-attempt transient-retry budget against a deterministic content error).
    if error_str.startswith("output validation failed:"):
        return AuthFailureClassification(status="unknown", reason="invalid_outputs")

    adapter_impl = get_auth_capable(lease.adapter)
    if adapter_impl is None:
        return AuthFailureClassification(status="unknown")

    debug_log_path = lease.slot_dir / "claude-code-debug.log"
    if debug_log_path.exists():
        try:
            debug_text = debug_log_path.read_text()
        except OSError:
            debug_text = ""
        if debug_text:
            # Append debug-log content AFTER the engine error string so that
            # forward-only negative lookaheads in known-bug regexes (e.g.
            # `claude-startup-exit-1-silent` in known_bugs.py) can correctly
            # exclude matches when diagnostic tokens like `429`,
            # `rate_limit_error`, or `error` appear in the debug log. Prepending
            # the debug text would hide those tokens behind the regex's match
            # position, causing rate-limit failures to be misclassified as
            # the silent-startup known bug and aborted without retry. See
            # arch-claude-code-harness.md § "False-positive classifier pitfall"
            # for the failure-mode documentation.
            error_str = f"{error_str}\n{debug_text}"

    return adapter_impl.classify_failure(None, error_str, session_log_path)


def auth_override_refusal_keys(adapter: str) -> tuple[str, ...]:
    """Return ambient auth vars that would override a pooled OAuth slot."""
    if adapter == "claude-code-cli":
        return ("ANTHROPIC_API_KEY",)
    if adapter == "codex-cli":
        return ("OPENAI_API_KEY",)
    return ()


def compose_slot_env(
    parent_env: dict[str, str],
    *,
    lease: SlotLease,
    refuse_on_keys: tuple[str, ...] = (),
) -> dict[str, str]:
    """Merge *parent_env* + slot scope/scrub and return the subprocess env.

    ``refuse_on_keys`` names higher-precedence auth vars whose
    presence in the parent env should make the caller refuse to
    acquire the slot — e.g. ``ANTHROPIC_API_KEY`` for Claude,
    ``OPENAI_API_KEY`` for Codex. This function returns the merged
    env with those keys present when the caller chose to ignore the
    precedence warning; the refusal itself is the caller's policy
    choice (parallel to the spec's coordinator-refuses-slot rule).
    """
    live_overrides = [k for k in refuse_on_keys if parent_env.get(k)]
    if live_overrides:
        names = ", ".join(sorted(live_overrides))
        log.error(
            "pool_dispatch: refusing pool slot because higher-precedence auth var(s) "
            "are present in parent env: %s",
            names,
        )
        msg = (
            "pool slot refused: ambient auth var(s) would override the pooled "
            f"OAuth credential: {names}"
        )
        raise PoolAuthOverrideError(msg)
    return apply_slot_env(
        parent_env,
        scope_env=lease.scope_env,
        scrub_env=lease.scrub_env,
    )


@dataclass
class AuthOutcome:
    """Per-item outcome event recorded alongside the step result.

    Written into the end-of-item event stream so the pool's operation
    is auditable from logs without reading the backend.

    Schema versions
    ---------------
    - **v1 (legacy, ``schema_version: 1``)**: original fields up to
      ``retry_after_s``.
    - **v2 (current, ``schema_version: 2``)**: adds the join keys
      ``run_id``, ``step_id``, ``item``, ``attempt``, ``session_log_path``
      so post-hoc analysis can pair acquisitions with outcomes by
      primary key instead of timestamp-window matching. v1 readers
      tolerate the additional fields (additive change); v2 readers
      see legacy events with the new fields defaulting to ``""`` /
      ``0`` / ``None``.

    Spec: docs/project/specs/active/plan-2026-05-03-auth-observability-and-load-balancing.md
    """

    schema_version: int = 2
    adapter: str = ""
    label: str = ""
    slot_dir: str = ""
    bootstrap_fp: str = ""
    flush_fp: str = ""
    classification: str = ""  # ok | cooling | expired | unknown
    cooling_until_ts: int | None = None
    rotated: bool = False
    retry_count: int = 0
    fallback_policy: str = ""
    reason: str = ""
    severity: str = ""
    known_bug_signature: str = ""
    pool_enabled: bool = True
    # HTTP-axis signals copied from the AuthFailureClassification.
    # Adapters that surface these from their CLI debug output populate
    # them; absent / None means "signal not applicable or not observed".
    request_id: str = ""
    api_status: int | None = None
    oauth_refresh_status: int | None = None
    error_body_excerpt: str = ""
    retry_after_s: int | None = None
    # Schema-v2 join keys. Empty string / 0 / None on legacy events.
    run_id: str = ""
    step_id: str = ""
    item: str = ""
    attempt: int = 0
    session_log_path: str = ""


def build_auth_outcome(
    lease: SlotLease,
    *,
    classification: AuthFailureClassification | None,
    flushed_blob: str | None,
    retry_count: int,
    fallback_policy: FallbackPolicy,
) -> AuthOutcome:
    """Compose an :class:`AuthOutcome` record from lease + post-run signals."""

    flush_fp = fingerprint_blob(flushed_blob) if flushed_blob is not None else ""
    rotated = bool(flushed_blob is not None and flush_fp != lease.bootstrap_fp)
    if classification is None:
        verdict = "ok"
        reason = ""
        ts = None
        severity = ""
        known_bug = ""
        request_id = ""
        api_status: int | None = None
        oauth_refresh_status: int | None = None
        error_body_excerpt = ""
        retry_after_s: int | None = None
    else:
        verdict = classification.status
        reason = classification.reason
        ts = classification.cooling_until_ts
        # Use effective_severity() so a known-bug always surfaces as
        # ABORT in the outcome even if severity wasn't set explicitly.
        severity = classification.effective_severity()
        known_bug = classification.known_bug_signature or ""
        request_id = classification.request_id
        api_status = classification.api_status
        oauth_refresh_status = classification.oauth_refresh_status
        error_body_excerpt = classification.error_body_excerpt
        retry_after_s = classification.retry_after_s
    return AuthOutcome(
        adapter=lease.adapter,
        label=lease.label,
        slot_dir=str(lease.slot_dir),
        bootstrap_fp=lease.bootstrap_fp,
        flush_fp=flush_fp,
        classification=verdict,
        cooling_until_ts=ts,
        rotated=rotated,
        retry_count=retry_count,
        fallback_policy=str(fallback_policy),
        reason=reason,
        severity=severity,
        known_bug_signature=known_bug,
        request_id=request_id,
        api_status=api_status,
        oauth_refresh_status=oauth_refresh_status,
        error_body_excerpt=error_body_excerpt,
        retry_after_s=retry_after_s,
        # Schema-v2 join keys, read off the lease.
        run_id=lease.run_id,
        step_id=lease.step_id,
        item=lease.item,
        attempt=lease.attempt,
        session_log_path=str(lease.session_log_path) if lease.session_log_path else "",
    )


def build_auth_lease_acquired(
    lease: SlotLease,
    *,
    policy: str = "",
    active_lease_count: dict[str, int] | None = None,
) -> dict[str, object]:
    """Compose the ``auth_lease_acquired`` event payload from a lease.

    Schema-v2 acquisition-time companion to :class:`AuthOutcome` —
    emitted by the slot coordinator before the subprocess starts so
    post-hoc analysis can pair acquisitions with outcomes by the
    five-key primary join (``run_id``/``step_id``/``item``/``attempt``/
    ``session_log_path``) instead of timestamp-window matching.

    ``policy`` is the dispatch's :class:`SelectionPolicy` value (e.g.
    ``"round-robin"``); recorded so post-hoc analysis can see the
    policy in effect at acquisition time without parsing run-config.
    Empty string when policy is unknown to the caller.

    ``active_lease_count`` is the snapshot of all
    ``(adapter, label) → count`` entries at the moment of acquisition.
    Lets observability tools inspect balance state per-event without
    replaying the full event stream. ``None`` when the caller has no
    counter (e.g. the legacy ``PRIORITY_ORDER`` path before
    ``ActiveLeaseCounter`` is wired).

    The returned dict is deliberately plain — :class:`EventLogger`
    stamps ``ts`` and the ``event`` discriminator on top.
    """
    return {
        "schema_version": 2,
        "adapter": lease.adapter,
        "label": lease.label,
        "slot_dir": str(lease.slot_dir),
        "run_id": lease.run_id,
        "step_id": lease.step_id,
        "item": lease.item,
        "attempt": lease.attempt,
        "session_log_path": (str(lease.session_log_path) if lease.session_log_path else ""),
        "policy": policy,
        "active_lease_count": dict(active_lease_count or {}),
    }


@dataclass(frozen=True)
class CredentialProbeResult:
    """Outcome of :func:`probe_credential` against a single credential blob.

    Carries the parsed signals + decided pool-state target plus enough
    raw context (exit code, stdout excerpt, debug log excerpt) for the
    caller to render an operator-actionable note.
    """

    signals: ClaudeApiSignals
    target_status: str  # active | expired | cooling | unknown
    note: str
    exit_code: int
    stdout: str
    debug_log: str


def probe_credential(
    adapter_impl: Any,
    blob: str,
    *,
    adapter_name: str,
    vehicle: Vehicle = Vehicle.LOGIN_CREDENTIALS,
    timeout_s: int = 60,
    sidecar_target: Path | None = None,
) -> CredentialProbeResult:
    """Run a real-API probe against ``blob``.

    Materializes the credential into a temp slot, runs the adapter's
    debug-capture-args + ``--print "OK"``, parses the slot-local
    debug log + stdout for HTTP signals, and maps the result to a
    pool-state target. Caller decides what to do with the result —
    operator-facing CLI surfaces it; pre-fan-out gating filters
    expired/cooling labels out of the dispatch strategy.

    The materialization + execution mirrors the dispatch path exactly
    (same scrub env, same scope env, same debug-file plumbing) so the
    probe sees what a worker would see. *vehicle* threads through the
    adapter's vehicle-aware materialize / scope / scrub triplet so a
    Vehicle A label probes against the bearer-token env-var path
    (no ``.credentials.json`` written) and a Vehicle B label probes
    against the slot-file path. Default of ``LOGIN_CREDENTIALS``
    matches the schema-v1 read-side compat assumption.

    Currently Claude-only: the command shape (``claude --print "OK"``),
    diagnostic filename (``claude-code-debug.log``), and HTTP-signal
    parser (:func:`parse_claude_api_signals`) are Claude-specific.
    Generic probing requires lifting these onto the adapter (e.g. an
    ``AuthCapableCliAdapter.probe_command`` + ``probe_diagnostic_filename``
    + a per-adapter signal parser). Until that lands, callers must
    pre-filter on adapter name; calling with another adapter raises
    :class:`NotImplementedError` rather than silently invoking
    ``claude`` against a non-Claude blob.
    """
    if adapter_name != "claude-code-cli":
        msg = (
            f"probe_credential: generic credential probing is not yet implemented; "
            f"got adapter_name={adapter_name!r}. Currently only 'claude-code-cli' is "
            f"supported (the command shape and HTTP-signal parser are Claude-specific). "
            f"See pool_dispatch.probe_credential docstring."
        )
        raise NotImplementedError(msg)

    with tempfile.TemporaryDirectory(prefix="mp-probe-") as slot_str:
        slot_dir = Path(slot_str)
        adapter_impl.materialize_credential(slot_dir, blob, vehicle=vehicle)
        scrub_env = {
            **adapter_impl.credential_scrub_env(vehicle=vehicle),
            **{key: "" for key in auth_override_refusal_keys(adapter_name)},
        }
        probe_env = apply_slot_env(
            dict(os.environ),
            scope_env=adapter_impl.credential_scope_env(slot_dir, vehicle=vehicle, blob=blob),
            scrub_env=scrub_env,
        )
        debug_args = list(adapter_impl.debug_capture_args(slot_dir))
        # Worker subprocess command shape mirrors what dispatch launches.
        cmd = ["claude", *debug_args, "--print", "OK"]
        if sidecar_target is not None:
            from metaproc.runpool.backend import (  # noqa: PLC0415 — circular-import guard
                write_invocation_sidecar,
            )

            write_invocation_sidecar(
                target=sidecar_target,
                argv=cmd,
                env=probe_env,
                cwd=None,
                metadata={"role": "preflight-probe", "adapter": adapter_name},
            )
        proc = subprocess.run(
            cmd,
            env=probe_env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        debug_log_path = slot_dir / "claude-code-debug.log"
        debug_text = debug_log_path.read_text() if debug_log_path.exists() else ""

    signals = parse_claude_api_signals(debug_text=debug_text, stdout=proc.stdout)

    # Classification: structured signals only — no substring matches on
    # arbitrary diagnostic text.
    if signals.oauth_refresh_status is not None and signals.oauth_refresh_status >= 400:
        target = "expired"
        note = (
            f"OAuth refresh returned {signals.oauth_refresh_status} — refresh token "
            f"rejected by Anthropic; pool blob needs `metaproc auth push --label <X>` "
            f"after re-auth."
        )
    elif signals.api_status == 401:
        target = "expired"
        note = "API returned 401 authentication_error; access token rejected."
    elif signals.api_status == 429:
        target = "cooling"
        retry_part = f" with retry_after={signals.retry_after_s}s" if signals.retry_after_s else ""
        note = f"API returned 429{retry_part}"
    elif proc.returncode == 0 and signals.api_status is None:
        target = "active"
        note = "claude --print returned successfully; access token is valid."
    else:
        target = "unknown"
        # Capture the full stderr to a known artifact for post-run inspection,
        # plus a head + tail summary in the note so the most likely informative
        # parts (the actual error message, usually at the start; the throw
        # site, usually at the end) both fit in the event stream.
        stderr_full = proc.stderr or ""
        stderr_artifact = slot_dir / "probe.stderr"
        try:
            stderr_artifact.write_text(stderr_full)
        except OSError:
            pass  # slot may already be torn down on the cleanup race
        stderr_clean = stderr_full.strip()
        stderr_head = stderr_clean.replace("\n", " | ")[:1200]
        stderr_tail = stderr_clean.replace("\n", " | ")[-1200:] if len(stderr_clean) > 1200 else ""
        stdout_clean = (proc.stdout or "").strip().replace("\n", " | ")[:400]
        note = (
            f"unexpected probe outcome (exit={proc.returncode}, "
            f"api_status={signals.api_status}). "
            f"stderr_head={stderr_head!r} stderr_tail={stderr_tail!r} "
            f"stdout={stdout_clean!r}"
        )

    return CredentialProbeResult(
        signals=signals,
        target_status=target,
        note=note,
        exit_code=proc.returncode,
        stdout=proc.stdout,
        debug_log=debug_text,
    )


def pre_fan_out_probe(
    pool_dispatch: PoolDispatchConfig,
    pool_backend: PoolBackend,
    *,
    on_probe: Any = None,
    sidecar_dir: Path | None = None,
) -> PoolDispatchConfig:
    """Probe each label in *pool_dispatch.strategy*; filter out failures.

    For every label in ``pool_dispatch.strategy.labels`` (or all entries
    for the adapter if labels is empty), runs :func:`probe_credential`.
    Failed probes:

    - ``expired`` → entry transitioned via :func:`state_mark_expired`,
      label removed from the returned strategy.
    - ``cooling`` → entry transitioned via :func:`state_mark_cooling`
      (best-effort cooling_until_ts from retry_after_s), label
      removed from the returned strategy.

    Returns a possibly-replaced :class:`PoolDispatchConfig` whose
    ``strategy.labels`` contains only the labels that probed
    ``active``. Raises :class:`PoolSlotUnavailableError` when every
    label fails so dispatch fails fast at startup rather than after
    burning a fan-out's worth of items.

    ``on_probe`` callback (optional) receives ``(label, result)`` tuples
    so callers can render per-label progress to operators.
    """

    # Diagnostic bypass: when METAPROC_SKIP_PREFLIGHT_PROBE=1, return the
    # pool config unchanged. Used during cloud tool-surface investigations
    # where we want process-item to spawn claude (and write its
    # invocation sidecar) even when alt1+alt2 are cooling. Probing would
    # itself burn quota and fail before fan-out had a chance to log the
    # claude argv we are trying to capture. Spec: plan-2026-05-01 § Phase 3.5.
    if os.environ.get("METAPROC_SKIP_PREFLIGHT_PROBE") == "1":
        log.warning(
            "pre_fan_out_probe: skipping (METAPROC_SKIP_PREFLIGHT_PROBE=1). "
            "This is a diagnostic-only bypass; expect items to fail at runtime "
            "if labels are actually unhealthy."
        )
        return pool_dispatch

    adapter_name = pool_dispatch.adapter
    adapter_impl = get_auth_capable(adapter_name)
    if adapter_impl is None:
        log.warning(
            "pre_fan_out_probe: adapter %r is not auth-capable; skipping probe",
            adapter_name,
        )
        return pool_dispatch
    # probe_credential is Claude-only today (command shape, diagnostic filename,
    # and HTTP-signal parser are all Claude-specific). Skip with a clear log
    # for any other adapter rather than calling probe_credential — its guard
    # would raise NotImplementedError, which is the wrong shape for the
    # preflight callsite (preflight should fail open, not crash dispatch).
    if adapter_name != "claude-code-cli":
        log.warning(
            "pre_fan_out_probe: adapter %r is not yet supported by the live probe "
            "(Claude-only today); skipping pre-fan-out gating",
            adapter_name,
        )
        return pool_dispatch

    candidate_labels = list(pool_dispatch.strategy.labels)
    if not candidate_labels:
        # Empty strategy means "consider every entry for this adapter".
        candidate_labels = sorted(e.label for e in pool_backend.list_entries(adapter=adapter_name))

    healthy_labels: list[str] = []
    for label in candidate_labels:
        try:
            entry = pool_backend.get_entry(adapter_name, label)
        except KeyError:
            log.warning("pre_fan_out_probe: no pool entry for %s/%s; skipping", adapter_name, label)
            continue
        if entry.state.status in ("expired", "disabled"):
            # Already known-bad; no need to burn a probe.
            log.info(
                "pre_fan_out_probe: skipping %s/%s (already %s)",
                adapter_name,
                label,
                entry.state.status,
            )
            if on_probe is not None:
                on_probe(label, None)
            continue
        sidecar_target: Path | None = None
        if sidecar_dir is not None:
            sidecar_target = sidecar_dir / f"{label}.invocation.json"
        result = probe_credential(
            adapter_impl,
            entry.blob,
            adapter_name=adapter_name,
            vehicle=entry.state.vehicle,
            sidecar_target=sidecar_target,
        )
        if on_probe is not None:
            on_probe(label, result)
        if result.target_status == "active":
            healthy_labels.append(label)
            # Persist the ACTIVE outcome to the pool state so workers (which
            # read pool state directly) see the label as eligible. Without
            # this write, an entry that was previously marked `cooling` —
            # e.g. by a prior failed dispatch's runtime classification —
            # stays cooling in pool state even though the live probe just
            # said it's healthy. The fan-out workers then can't acquire it
            # and dispatch fails with "no eligible pool label" despite the
            # in-process strategy showing it as healthy.
            # See plan-2026-05-01 § Phase 3.5 (cloud-validate-2 incident
            # 2026-05-04 17:33 → 19:09).
            safe_apply_state(
                pool_backend,
                adapter=adapter_name,
                label=label,
                entry=entry,
                compute_new=state_mark_ok,
            )
        elif result.target_status == "expired":
            safe_apply_state(
                pool_backend,
                adapter=adapter_name,
                label=label,
                entry=entry,
                compute_new=state_mark_expired,
            )
        elif result.target_status == "cooling":
            cooling_until = None
            if result.signals.retry_after_s is not None:
                cooling_until = int(_time.time()) + int(result.signals.retry_after_s)
            safe_apply_state(
                pool_backend,
                adapter=adapter_name,
                label=label,
                entry=entry,
                compute_new=lambda s, until=cooling_until: state_mark_cooling(
                    s, cooling_until_ts=until
                ),
            )
        # `unknown` → leave the entry alone but skip the label for this
        # dispatch; an unknown probe outcome is fail-safe (don't gate it
        # active), but also not authoritative enough to flip pool state.

    if not healthy_labels:
        raise PoolSlotUnavailableError(
            adapter_name,
            policy=pool_dispatch.fallback_policy,
            excluded=tuple((adapter_name, lbl) for lbl in candidate_labels),
        )

    new_strategy = SelectionStrategy(
        policy=pool_dispatch.strategy.policy,
        labels=tuple(healthy_labels),
    )
    return replace(pool_dispatch, strategy=new_strategy)


__all__ = [
    "AuthOutcome",
    "CredentialProbeResult",
    "PoolAuthOverrideError",
    "PoolDispatchConfig",
    "PoolSlotUnavailableError",
    "acquire_slot",
    "auth_override_refusal_keys",
    "build_auth_lease_acquired",
    "build_auth_outcome",
    "classify_failure_for_slot",
    "compose_slot_env",
    "pre_fan_out_probe",
    "probe_credential",
]
