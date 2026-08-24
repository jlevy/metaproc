"""Per-item credential slot coordinator.

Bridges :mod:`metaproc.dispatch.credential_pool` (selection + health)
to the worker subprocess launch machinery in
:mod:`metaproc.commands.run_parallel` (per-item env + slot directory
lifecycle).

Each item calls :meth:`SlotCoordinator.acquire_slot` to:

1. Select an eligible credential via :func:`select_credential`
   (within-adapter strategy) or :func:`select_fallback`
   (cross-adapter under :class:`FallbackPolicy`).
2. Materialize the blob into a per-attempt slot directory under
   ``<RUNS_DIR>/<run_id>/.state/auth/<step>/<item>/a<attempt>/`` via the
   adapter's ``materialize_credential``.
3. Return the scope env (``CLAUDE_CONFIG_DIR`` / ``CODEX_HOME`` /
   ``GEMINI_CONFIG_DIR``) and scrub env the caller merges into the
   subprocess env.

On item exit the coordinator:

1. Flushes the refreshed credential and writes back to the pool if the
   fingerprint changed (``write_back_rotated``).
2. Marks the label ``ok`` / ``cooling`` / ``expired`` per the failure
   classifier.
3. ``rm -rf``s the slot dir.

Concurrency is owned by :class:`metaproc.runpool.pool.RunPool`; many
concurrent items can share the same label simultaneously. The
coordinator is stateless — no per-item bookkeeping beyond what the
returned :class:`SlotLease` carries.
"""

from __future__ import annotations

import hashlib
import logging
import random
import shutil
import time
import time as _time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from metaproc.adapters.base import AuthCapableCliAdapter, AuthFailureClassification
from metaproc.adapters.registry import get_auth_capable
from metaproc.config.env_vars import MetaprocEnv
from metaproc.dispatch.credential_pool import (
    ActiveLeaseCounter,
    AtomicCounter,
    ConcurrentModificationError,
    FallbackPolicy,
    PoolBackend,
    SelectionStrategy,
    eligible_labels,
    mark_cooling,
    mark_expired,
    mark_ok,
    select_credential,
    select_fallback,
    write_back_rotated,
)
from metaproc.dispatch.credential_pool import Vehicle as _Vehicle
from metaproc.io.mkdir_lock import (
    MkdirLockTimeoutError,
    acquire_mkdir_lock,
    release_mkdir_lock,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

log = logging.getLogger(__name__)


# Env var set on every launched subprocess so the worker entrypoint's
# one-time bootstrap(home) becomes a no-op when the pool already
# materialized the credential. Read by the Claude + Codex adapter
# bootstrap paths; keeping this as an env var avoids worker imports
# of the coordinator. Sourced from the typed registry so a rename in
# env_vars.py automatically propagates here.
SLOT_ACTIVE_ENV_VAR = MetaprocEnv.METAPROC_AUTH_POOL_RUN.name


@dataclass(frozen=True)
class SlotLease:
    """Per-item credential materialization handle.

    ``scope_env`` and ``scrub_env`` are returned separately so the
    launcher can enforce the scrub contract (values equal to ``""``
    mean "unset this key") without losing track of which keys came
    from which source. The dispatch-side helper :func:`apply_slot_env`
    merges them correctly onto a parent env.

    ``bootstrap_fp`` is the fingerprint of the blob as handed to the
    slot, so :meth:`SlotCoordinator.teardown` can decide whether to
    write back without re-reading the pool.

    ``holder`` is a debug string of the form
    ``<run_id>:<step>:<item>:a<attempt>``. It does not gate concurrency
    — multiple items can share the same label simultaneously.

    ``run_id`` / ``step_id`` / ``item`` / ``attempt`` /
    ``session_log_path`` are the schema-v2 join keys (spec
    plan-2026-05-03). They duplicate the components of ``holder``
    in structured form so :class:`AuthOutcome` and the
    ``auth_lease_acquired`` event can carry them as primary-key fields
    without re-parsing the holder string. ``session_log_path`` is
    ``None`` only on legacy SlotLeases constructed before run_parallel
    threaded the path through (e.g. the in-tree probe path that doesn't
    drive a real session log).
    """

    adapter: str
    label: str
    slot_dir: Path
    holder: str
    scope_env: dict[str, str]
    scrub_env: dict[str, str]
    bootstrap_fp: str
    # V-B safe mode: when the lease was acquired against a Vehicle B
    # credential, this is the path to the per-label mkdir-lock that
    # serializes refresh-window access for that label across attempts on
    # this host. teardown removes the lock dir. None for V-A leases
    # (no refresh writeback path).
    label_lock_path: Path | None = None
    # Schema-v2 join keys.
    run_id: str = ""
    step_id: str = ""
    item: str = ""
    attempt: int = 0
    session_log_path: Path | None = None


def build_holder_id(run_id: str, step: str, item: str, attempt: int) -> str:
    """Canonical holder string for a slot — debug/audit only."""
    return f"{run_id}:{step}:{item}:a{attempt}"


def slot_dir_for(runs_dir: Path, run_id: str, step: str, item: str, attempt: int) -> Path:
    """Canonical slot directory for a per-item attempt.

    ``<runs_dir>/<run_id>/.state/auth/<step>/<item>/a<attempt>/``. Lives
    under the run's ``.state/`` tree alongside other run-private state
    (``.state/orchestrator-lease.yaml``, ``.state/runpool-status.yaml``,
    ``.state/scale-state.yaml``) — operator's mental model is "anything
    private to this run is under ``.state/``", and ``**/.state/`` is the
    one path covered by the project's `.gitignore`.

    Each retry attempt gets a fresh directory so a failed attempt's
    slot is never mutated by the retry — needed so flush_refreshed_
    credential can cleanly compare the pre- and post-spawn blobs.

    Item names may contain characters (``/``, spaces) that are awkward
    in filesystem paths; we hash long or suspicious names to keep the
    filesystem safe while staying reproducible.
    """
    safe_item = _safe_path_segment(item)
    safe_step = _safe_path_segment(step)
    return runs_dir / run_id / ".state" / "auth" / safe_step / safe_item / f"a{attempt}"


def vehicle_b_lock_dir() -> Path:
    """Return the directory under which Vehicle B per-label locks live.

    Defaults to ``~/.metaproc/auth-pool/locks``. Overridable via
    ``METAPROC_AUTH_POOL_LOCK_DIR`` so cloud workers sharing a
    filestore mount can co-locate their lock state under it (without
    that override, two workers on the same host see each other's locks
    via $HOME but workers on different VMs do not).

    In V-B safe mode the per-label lock is what serializes refresh-window
    access on a single host. Cross-host coordination requires pointing
    this at a shared mount.
    """
    override = (
        MetaprocEnv.METAPROC_AUTH_POOL_LOCK_DIR.read_str(default="")
        if hasattr(MetaprocEnv, "METAPROC_AUTH_POOL_LOCK_DIR")
        else ""
    )
    if override:
        return Path(override)
    return Path.home() / ".metaproc" / "auth-pool" / "locks"


def vehicle_b_label_lock_path(adapter: str, label: str) -> Path:
    """Resolve the per-label lock path for a Vehicle B label.

    Stable across attempts so two parallel dispatches against the
    same label serialize through the same dir. Sanitized via the
    same path-segment rule slot dirs use, so labels with awkward
    characters don't break the lock filesystem layout.
    """
    return vehicle_b_lock_dir() / _safe_path_segment(adapter) / f"{_safe_path_segment(label)}.lock"


def _vehicle_b_lock_timeout_s() -> float:
    """Resolve the V-B per-label lock timeout (seconds).

    Generous default (300s) — V-B leases run a full subprocess inside
    the lock, so the wait at the head of the queue could easily be
    minutes during a fan-out. Operators tighten via
    ``METAPROC_AUTH_POOL_LOCK_TIMEOUT_S`` for diagnostic dispatches
    that should fail fast rather than queue.
    """
    if hasattr(MetaprocEnv, "METAPROC_AUTH_POOL_LOCK_TIMEOUT_S"):
        return float(MetaprocEnv.METAPROC_AUTH_POOL_LOCK_TIMEOUT_S.read_int(default=300))
    return 300.0


def _acquire_vehicle_b_label_lock(lock_path: Path) -> None:
    """Acquire the Vehicle B per-label lock via the shared mkdir-lock helper.

    Polls until acquired or the timeout elapses; reclaims stale locks
    older than the Vehicle B stale threshold.
    The release side is :func:`_release_vehicle_b_label_lock` (called
    from teardown). Acquisition and release are split across two coordinator
    calls (``acquire_slot`` and ``teardown``), so this path uses
    ``acquire_mkdir_lock`` rather than the context-manager wrapper.
    """
    timeout_s = _vehicle_b_lock_timeout_s()
    try:
        acquire_mkdir_lock(
            lock_path,
            timeout=timeout_s,
            stale_after=600.0,  # 10 min — longer than any legit single lease
        )
    except MkdirLockTimeoutError as exc:
        msg = (
            f"V-B safe-mode: could not acquire per-label lock {lock_path} "
            f"within {timeout_s:.0f}s (held by another dispatch). The "
            f"adapter and label combination is fan-out-saturated; either "
            f"wait, dispatch with a different label, or remove the lock "
            f"directory if you confirm no live process holds it."
        )
        raise MkdirLockTimeoutError(msg) from exc


def _release_vehicle_b_label_lock(lock_path: Path) -> None:
    """Release the V-B per-label lock by rmdir'ing ``lock_path``.

    Idempotent — missing-or-already-released is treated as success.
    Errors are logged at warning level since a stuck lock blocks
    future leases on this label until ``stale_after`` reclaims it.
    """
    try:
        release_mkdir_lock(lock_path)
    except OSError:
        log.warning(
            "V-B safe-mode: failed to release per-label lock %s; "
            "stale-after will reclaim it after timeout",
            lock_path,
            exc_info=True,
        )


def _safe_path_segment(segment: str) -> str:
    """Return a filesystem-safe segment for *segment*.

    Short, plain-alnum-and-dashes segments pass through. Anything
    else is replaced with a deterministic ``<prefix>-<sha12>`` form
    so the slot dir stays reconstructable from logs.
    """
    if (
        0 < len(segment) <= 64
        and all(c.isalnum() or c in "-_." for c in segment)
        and not segment.startswith(".")
    ):
        return segment
    digest = hashlib.sha256(segment.encode("utf-8")).hexdigest()[:12]
    # Keep a human-readable prefix (first 16 safe chars) so logs
    # are still greppable.
    prefix = "".join(c for c in segment[:16] if c.isalnum() or c in "-_") or "x"
    return f"{prefix}-{digest}"


class SlotCoordinator:
    """Acquires + materializes + tears down credential slots.

    Stateless: instances are cheap, no per-item state carried across
    calls. Tests instantiate a fresh coordinator per fixture.
    """

    def __init__(
        self,
        backend: PoolBackend,
        *,
        adapter_registry: Mapping[str, object] | None = None,
        active_counter: ActiveLeaseCounter | None = None,
        rr_counter: AtomicCounter | None = None,
    ) -> None:
        self._backend = backend
        self._registry = adapter_registry
        # Counters for ROUND_ROBIN / LEAST_ACTIVE policies. Each
        # SlotCoordinator owns one instance of each — they are scoped
        # to the dispatch (per (adapter, run)) at construction time.
        # Auto-instantiated when omitted so PRIORITY_ORDER callers
        # don't need to pass anything.
        self._active_counter = active_counter or ActiveLeaseCounter()
        self._rr_counter = rr_counter or AtomicCounter()

    @property
    def active_counter(self) -> ActiveLeaseCounter:
        """Expose the active-lease counter for observability snapshots."""
        return self._active_counter

    @property
    def backend(self) -> PoolBackend:
        """Expose the configured backend for read-only coordination queries."""
        return self._backend

    # ── Slot acquisition ────────────────────────────────────────

    def acquire_slot(
        self,
        adapter: str,
        *,
        runs_dir: Path,
        run_id: str,
        step: str,
        item: str,
        attempt: int,
        strategy: SelectionStrategy = SelectionStrategy(),
        exclude: tuple[tuple[str, str], ...] = (),
        fallback_policy: FallbackPolicy = FallbackPolicy.NONE,
        session_log_path: Path | None = None,
    ) -> SlotLease | None:
        """Select an eligible credential and materialize a slot.

        ``strategy`` selects within the requested adapter. When that
        exhausts and ``fallback_policy`` is non-NONE, the coordinator
        walks to compatible adapters via :func:`select_fallback`.

        ``exclude`` is the set of (adapter, label) pairs the caller
        wants skipped — typically labels that already failed on this
        item's earlier attempts.

        Returns ``None`` when nothing is eligible; the caller decides
        whether to fail or reschedule the item.
        """
        holder = build_holder_id(run_id=run_id, step=step, item=item, attempt=attempt)
        attempted: set[tuple[str, str]] = set(exclude)

        while True:
            selection = select_credential(
                self._backend,
                adapter,
                strategy=strategy,
                exclude_labels=attempted,
                active_counter=self._active_counter,
                rr_counter=self._rr_counter,
            )
            if selection is None and fallback_policy != FallbackPolicy.NONE:
                selection = select_fallback(
                    self._backend,
                    adapter,
                    exclude_labels=attempted,
                    policy=fallback_policy,
                    adapter_registry=self._registry or {},
                )
            if selection is None:
                return None

            adapter_impl = self._resolve_adapter(selection.adapter)
            if adapter_impl is None:
                # Selection landed on an adapter that isn't registered
                # or isn't auth-capable. Skip and walk forward.
                log.warning(
                    "slot_coordinator: adapter %r not auth-capable; skipping",
                    selection.adapter,
                )
                attempted.add((selection.adapter, selection.label))
                continue

            slot_dir = slot_dir_for(
                runs_dir=runs_dir,
                run_id=run_id,
                step=step,
                item=item,
                attempt=attempt,
            )
            # V-B safe mode: for Vehicle B leases, acquire a per-label
            # mkdir-lock BEFORE materializing so two parallel attempts on
            # the same V-B label serialize through one access-token-
            # refresh window. V-A leases have no refresh writeback path,
            # so no lock.
            label_lock_path: Path | None = None
            if selection.vehicle is _Vehicle.LOGIN_CREDENTIALS:
                label_lock_path = vehicle_b_label_lock_path(selection.adapter, selection.label)
                _acquire_vehicle_b_label_lock(label_lock_path)
            try:
                # Vehicle-aware materialization: pass the selection's
                # vehicle and blob through to the adapter so Vehicle A
                # entries get env-var-only injection (no .credentials.json
                # written) and Vehicle B entries get the legacy slot-file
                # path.
                adapter_impl.materialize_credential(
                    slot_dir, selection.blob, vehicle=selection.vehicle
                )
            except Exception:
                # If materialize raises, release the V-B lock on the way
                # out so the label isn't stuck blocked on a crashed
                # bootstrap.
                if label_lock_path is not None:
                    _release_vehicle_b_label_lock(label_lock_path)
                raise

            # Acquisition-time counter +1. Pair with -1 in
            # :meth:`teardown` so success and failure release
            # symmetrically. ROUND_ROBIN doesn't read this counter, but
            # we increment unconditionally so the snapshot embedded in
            # ``auth_lease_acquired`` events accurately reflects all
            # in-flight leases regardless of policy.
            self._active_counter.acquire(selection.adapter, selection.label)
            return SlotLease(
                adapter=selection.adapter,
                label=selection.label,
                slot_dir=slot_dir,
                holder=holder,
                scope_env=adapter_impl.credential_scope_env(
                    slot_dir, vehicle=selection.vehicle, blob=selection.blob
                ),
                scrub_env=adapter_impl.credential_scrub_env(vehicle=selection.vehicle),
                bootstrap_fp=selection.fingerprint,
                label_lock_path=label_lock_path,
                run_id=run_id,
                step_id=step,
                item=item,
                attempt=attempt,
                session_log_path=session_log_path,
            )

    # ── Slot teardown ───────────────────────────────────────────

    def teardown(
        self,
        lease: SlotLease,
        *,
        failure: AuthFailureClassification | None = None,
    ) -> str | None:
        """Write back refreshed creds, transition state, clean up.

        Failure-class behavior:
          * ``None`` / ``ok`` — flush the on-disk blob, write back if
            its fingerprint rotated; otherwise just bump ``last_ok_ts``.
          * ``cooling`` — :func:`mark_cooling` records the reset ts so
            selection skips this label until then. Concurrent items
            already in flight on this label finish their work.
          * ``expired`` — :func:`mark_expired` requires operator action.
          * ``unknown`` — defer to the generic retry classifier; pool
            state unchanged.

        ``rm -rf``s the slot dir on the way out so failed attempts get
        a fresh ``a<attempt+1>/`` on retry. The caller is responsible
        for calling :meth:`preserve_diagnostics` first if it wants the
        slot's diagnostic files (``claude-code-debug.log``, …) preserved
        next to the run's session log — teardown itself never copies
        anything out. Idempotent.

        Returns the flushed blob (or ``None``) so the caller can stamp
        post-run fingerprints into the ``auth_outcome`` event.
        """
        adapter_impl = self._resolve_adapter(lease.adapter)

        flushed_blob: str | None = None
        try:
            try:
                if failure is None or failure.status == "ok":
                    if adapter_impl is not None:
                        flushed_blob = adapter_impl.flush_refreshed_credential(lease.slot_dir)
                        if flushed_blob is not None:
                            write_back_rotated(
                                self._backend,
                                lease.adapter,
                                lease.label,
                                new_blob=flushed_blob,
                            )
                        else:
                            mark_ok(self._backend, lease.adapter, lease.label)
                elif failure.status == "cooling":
                    mark_cooling(
                        self._backend,
                        lease.adapter,
                        lease.label,
                        cooling_until_ts=failure.cooling_until_ts,
                        reason=failure.reason,
                    )
                elif failure.status == "expired":
                    mark_expired(
                        self._backend,
                        lease.adapter,
                        lease.label,
                        reason=failure.reason,
                    )
            except ConcurrentModificationError:
                log.warning(
                    "slot_coordinator: pool state changed during teardown for %s/%s",
                    lease.adapter,
                    lease.label,
                )
        finally:
            try:
                if lease.slot_dir.exists():
                    shutil.rmtree(lease.slot_dir)
            except OSError:
                log.warning(
                    "slot_coordinator: failed to rm -rf %s",
                    lease.slot_dir,
                    exc_info=True,
                )
            if lease.label_lock_path is not None:
                _release_vehicle_b_label_lock(lease.label_lock_path)
            self._active_counter.release(lease.adapter, lease.label)
        return flushed_blob

    def preserve_diagnostics(self, lease: SlotLease, target_log_path: Path) -> None:
        """Copy adapter-declared diagnostic files out before teardown.

        The set of files preserved is whatever the lease's adapter
        returns from :meth:`AuthCapableCliAdapter.diagnostic_filenames`
        (e.g. ``("claude-code-debug.log",)`` for Claude Code; ``()``
        for codex / gemini until they grow equivalents). Anything not
        in that set is treated as cred-bearing and stays inside
        ``slot_dir`` to be wiped by ``teardown``.

        Files land alongside the step's session log: given
        ``target_log_path = .../<step>_<item>_<ts>.jsonl``, each
        diagnostic file ``<name>`` becomes
        ``.../<step>_<item>_<ts>.<name>`` so the operator-facing
        ``.logs/`` tree shows session log + every adapter's diagnostic
        side-by-side in lexical order.

        Best-effort: any exception is logged and swallowed —
        diagnostic preservation must never block credential cleanup
        or fail a run. Idempotent: missing source files are a no-op.
        """
        if not lease.slot_dir.exists():
            return
        adapter_impl = self._resolve_adapter(lease.adapter)
        if adapter_impl is None:
            return
        filenames = tuple(adapter_impl.diagnostic_filenames())
        if not filenames:
            return
        target_dir = target_log_path.parent
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            log.warning("slot_coordinator: failed to create logs dir %s", target_dir, exc_info=True)
            return
        for name in filenames:
            src = lease.slot_dir / name
            if not src.exists():
                continue
            target = target_dir / f"{target_log_path.stem}.{name}"
            try:
                shutil.copy2(src, target)
            except OSError:
                log.warning(
                    "slot_coordinator: failed to copy %s -> %s",
                    src,
                    target,
                    exc_info=True,
                )

    # ── Internal helpers ────────────────────────────────────────

    def _resolve_adapter(self, adapter_name: str) -> AuthCapableCliAdapter | None:
        """Resolve *adapter_name* via the injected registry or the global."""
        if self._registry is not None:
            cand = self._registry.get(adapter_name)
            if isinstance(cand, AuthCapableCliAdapter):
                return cand
            if cand is not None:
                return None
        return get_auth_capable(adapter_name)

    # ── In-dispatch wait mode ────────────────────────────────────

    def wait_for_pool_recovery(
        self,
        adapter: str,
        *,
        max_wait_s: int = 6 * 60 * 60,
        poll_interval_s: int = 300,
        sleeper: Any | None = None,
        clock: Any | None = None,
    ) -> bool:
        """Block until any label on *adapter* becomes eligible.

        Sleeps to ``min(cooling_until_ts_earliest, now + poll_interval_s)``
        plus a small jitter, then re-probes the pool. Returns ``True``
        as soon as at least one label is eligible. Returns ``False``
        when *max_wait_s* elapses without recovery (caller raises a
        RetryLaterError / fails fast).

        ``sleeper`` and ``clock`` are injection seams for tests so
        we don't actually sleep. Production passes ``time.sleep`` /
        ``time.time``.
        """
        sleep_fn = sleeper if sleeper is not None else time.sleep
        clock_fn = clock if clock is not None else time.time

        deadline = clock_fn() + max_wait_s
        while clock_fn() < deadline:
            entries = self._backend.list_entries(adapter=adapter)
            now_int = int(clock_fn())
            if eligible_labels(entries, now=now_int):
                return True
            # Earliest cooling_until across cooling labels.
            earliest = None
            for e in entries:
                ts = e.state.cooling_until_ts
                if ts is not None and (earliest is None or ts < earliest):
                    earliest = ts
            sleep_target = poll_interval_s
            if earliest is not None:
                to_earliest = max(0, earliest - now_int)
                sleep_target = min(sleep_target, to_earliest)
            # Jitter 30-120s per spec so a fleet of wait-mode
            # dispatches doesn't thundering-herd the same reset.
            jitter = random.uniform(30, 120)
            sleep_fn(sleep_target + jitter)
        return False


def apply_slot_env(
    parent_env: dict[str, str],
    *,
    scope_env: dict[str, str],
    scrub_env: dict[str, str],
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Merge a parent env + slot scope/scrub + caller's extras.

    Values equal to ``""`` in *scrub_env* are interpreted as "unset
    this key" — they drop out of the returned dict. All other values
    land in the result; *scope_env* and *extra* win over *parent_env*.
    The ``METAPROC_AUTH_POOL_RUN=1`` signal is always set so the
    worker entrypoint's legacy bootstrap(home) becomes a no-op.
    """
    merged: dict[str, str] = dict(parent_env)
    for k, v in scrub_env.items():
        if v == "":
            merged.pop(k, None)
        else:
            merged[k] = v
    merged.update(scope_env)
    if extra:
        merged.update(extra)
    merged[SLOT_ACTIVE_ENV_VAR] = "1"
    return merged


@dataclass
class StartupFailureCircuit:
    """Pool-level circuit breaker for the startup-crash pattern.

    Trips when N consecutive workers exit code 1 in under
    ``short_exit_s`` without any successful completion interleaved.
    The signature is distinctive: real LLM work runs 10-22 min, but a
    rate-limit-rejected CLI exits in 15-45s with exit 1. Ordinary
    transient failures don't trip the breaker because they interleave
    with successes.

    The breaker is advisory: ``should_trip()`` just reports; the
    RunPool decides whether to swap the pool-default label, call
    mark_cooling, and re-stage new spawns. Keeping the decision out
    of the circuit itself means the caller stays in control of the
    lease-swap semantics.
    """

    window_s: int = 120
    threshold: int = 3
    short_exit_s: int = 60
    # Internal state — list of (timestamp, exit_code, duration_s).
    _recent: list[tuple[float, int, float]] = field(default_factory=list)

    def record_exit(self, duration_s: float, exit_code: int, *, now: float | None = None) -> None:
        """Record a worker exit. Ages out entries beyond ``window_s``."""

        t = now if now is not None else _time.time()
        self._recent.append((t, exit_code, duration_s))
        # Keep the list bounded to the window so memory stays flat
        # under long-running dispatches.
        cutoff = t - self.window_s
        self._recent = [r for r in self._recent if r[0] >= cutoff]

    def should_trip(self, *, now: float | None = None) -> bool:
        """Return True iff the last ``threshold`` exits were short failures
        with no successful completion interleaved.
        """

        t = now if now is not None else _time.time()
        cutoff = t - self.window_s
        relevant = [r for r in self._recent if r[0] >= cutoff]
        if len(relevant) < self.threshold:
            return False
        tail = relevant[-self.threshold :]
        return all(
            exit_code == 1 and duration_s < self.short_exit_s
            for (_ts, exit_code, duration_s) in tail
        )

    def reset(self) -> None:
        """Clear state; called after a successful swap so the new label
        starts with a clean slate."""
        self._recent = []


__all__ = [
    "SLOT_ACTIVE_ENV_VAR",
    "SlotCoordinator",
    "SlotLease",
    "StartupFailureCircuit",
    "apply_slot_env",
    "build_holder_id",
    "slot_dir_for",
]
