"""Single-source-of-truth aggregator for credential-pool usage signals.

Spec: ``docs/arch/arch-metaproc-core.md``


Pulls every signal an operator needs to answer "which accounts are
live, how much have they used, and is the dispatch using them as I
expect?" from the five on-disk sources the spec enumerates and
returns a per-(adapter, label) :class:`LabelUsage` record.

Sources (none authoritative on its own):

1. Pool state file (``~/.metaproc/auth-pool/<adapter>/<label>.yaml``
   or GCP Secret Manager) — last-known cooling-until, fingerprint,
   last_ok_ts, last_quota_ts, vehicle.
2. Runpool ``events.jsonl`` / legacy ``runpool-events.jsonl`` ``auth_outcome`` events — invocation
   counts, classifications, known-bug signatures.
3. Runpool ``events.jsonl`` / legacy ``runpool-events.jsonl`` ``auth_lease_acquired`` events
   (schema-v2) — primary-key joins to per-session JSONLs.
4. Per-session JSONLs ``rate_limit_event`` records — utilization +
   resetsAt for 5h / 7d windows.
5. Live probe (only when explicitly requested via ``--probe``).

The aggregator reads JSONL through the shared compressed-stream helpers —
same pattern as :mod:`metaproc.commands.pool` :func:`_collect_sub_pools`.
Typed pydantic models for ``auth_outcome`` / ``auth_lease_acquired``
are a Phase 4 follow-up; until they land,
:func:`metaproc.runpool.event_reader.read_runpool_events` silently
drops these events, so this module raw-parses and the typed reader
stays the path for the seven event types it does know.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from metaproc import paths as paths_mod
from metaproc.dispatch.credential_pool import PoolBackend, PoolEntry
from metaproc.io import (
    iter_artifact_paths,
    iter_jsonl_objects,
    resolve_existing_artifact,
)

log = logging.getLogger(__name__)


# ── Data shapes ─────────────────────────────────────────────────


PoolStateLiteral = Literal["active", "cooling", "expired", "disabled", "unknown"]
ProbeStateLiteral = Literal["active", "cooling", "expired", "unknown"]
RateLimitStatusLiteral = Literal["allowed", "allowed_warning", "rejected", "blocked"]
RateLimitWindowKind = Literal["one_hour", "five_hour", "seven_day"]


@dataclass(frozen=True)
class RateLimitWindow:
    """Most-recent ``rate_limit_event`` for one window (5h / 7d / 1h).

    ``utilization`` is the Anthropic-reported 0.0–1.0 fraction;
    ``resets_at`` is the unix epoch when the window resets.
    ``status`` is the upstream ``status`` field from the event
    (``allowed`` / ``allowed_warning`` / ``rejected`` / ``blocked``).
    """

    kind: RateLimitWindowKind
    status: str
    utilization: float | None
    resets_at: int | None


@dataclass(frozen=True)
class LabelUsage:
    """Everything we know about a single (adapter, label) pair.

    Built once per ``aggregate_label_usage`` call from all five
    on-disk sources, passed around to consumers (status, usage,
    doctor, preflight). Frozen so consumers can't mutate one
    label's view while iterating over others.
    """

    adapter: str
    label: str

    # ── From pool state file ──
    vehicle: str
    fingerprint: str
    pool_state: PoolStateLiteral
    cooling_until: datetime | None
    last_ok_ts: datetime | None
    last_quota_ts: datetime | None

    # ── From per-session JSONL rate_limit_event (most-recent per window) ──
    one_hour: RateLimitWindow | None
    five_hour: RateLimitWindow | None
    seven_day: RateLimitWindow | None
    last_event_ts: datetime | None

    # ── From runpool auth_outcome aggregation ──
    invocations_total: int
    invocations_ok: int
    invocations_failed: int
    known_bugs: dict[str, int]
    last_used_ts: datetime | None
    last_run_id: str | None

    # ── From a fresh probe (only populated when --probe is requested) ──
    probe_state: ProbeStateLiteral | None
    probe_request_id: str | None
    probe_age_s: int | None

    # ── Cross-source consistency (populated by doctor) ──
    inconsistencies: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LabelAttribution:
    """Resolves which (adapter, label) was leased for a session JSONL.

    Returned by :func:`attribute_session_to_label`. ``via`` records
    which join path produced the answer:

    - ``primary_key`` (schema-v2): the session_log_path matched an
      ``auth_lease_acquired`` event's recorded ``session_log_path``
      field. Unambiguous.
    - ``timestamp_window`` (schema-v1 fallback): no v2 event
      matched, so the attribution fell back to a timestamp-window
      heuristic against legacy ``auth_outcome`` events. Best-effort
      only; emit a warning when relied on.
    - ``unknown``: nothing matched. Caller decides whether to skip
      or surface a "no attribution" hint.
    """

    adapter: str
    label: str
    via: Literal["primary_key", "timestamp_window", "unknown"]
    lease_event: dict[str, Any] | None = None
    outcome_event: dict[str, Any] | None = None


# ── Outcome aggregation ────────────────────────────────────────


@dataclass
class _OutcomeStats:
    """Mutable accumulator used while folding auth_outcome events."""

    total: int = 0
    ok: int = 0
    failed: int = 0
    known_bugs: Counter[str] = field(default_factory=Counter)
    last_used_ts: datetime | None = None
    last_run_id: str | None = None

    def add(self, ev: dict[str, Any]) -> None:
        classification = ev.get("classification", "")
        self.total += 1
        if classification == "ok":
            self.ok += 1
        else:
            self.failed += 1
        bug = ev.get("known_bug_signature", "") or ""
        if bug:
            self.known_bugs[bug] += 1
        ts = _parse_ts(ev.get("ts"))
        if ts is not None and (self.last_used_ts is None or ts > self.last_used_ts):
            self.last_used_ts = ts
        run_id = ev.get("run_id", "") or ""
        if run_id:
            # Track last run we saw this label in. Sorting by ts is
            # already in the loop's contract — overwrite carries the
            # latest seen.
            self.last_run_id = run_id


def _parse_ts(raw: Any) -> datetime | None:
    """Parse the ``ts`` field on a runpool event into a UTC datetime.

    Events are written with ``datetime.now(UTC).isoformat(timespec="seconds")``
    (see :class:`metaproc.runpool.events.EventLogger`), so the raw
    string ends in ``+00:00``. Tolerates ``Z`` suffix for hand-edited
    fixtures.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


# ── Rate-limit parsing ─────────────────────────────────────────


def _parse_session_rate_limits(
    session_log_path: Path,
) -> tuple[dict[str, tuple[RateLimitWindow, datetime | None]], datetime | None]:
    """Return the most-recent ``rate_limit_event`` per window from one JSONL.

    The Claude Agent SDK emits one record per window-state change,
    so the file may contain multiple entries for ``five_hour`` /
    ``seven_day`` over a long-running session. We keep the latest
    seen (newest ``ts``) **per window kind**, returning the per-window
    timestamp alongside the window so the aggregator can merge across
    sessions deterministically. Records without a ``ts`` field stay
    last-write-wins within their own session (matches today's
    ``claude_code.py:1124`` logic).

    Returns ``({}, None)`` for missing / unreadable files —
    operationally common (a session that never tripped a rate-limit
    has no ``rate_limit_event`` records, and the file may be gzipped
    away on archived runs).
    """
    session_log_path = resolve_existing_artifact(session_log_path)
    if not session_log_path.is_file():
        return {}, None
    # Per window: (window, event_ts). Carrying the per-window ts is
    # what fixes a review finding — previously the
    # aggregator overwrote windows based on a single per-session ts,
    # which let an older session's high-utilization 5h record clobber
    # a newer session's low-utilization 5h record by set-iteration
    # luck.
    windows: dict[str, tuple[RateLimitWindow, datetime | None]] = {}
    latest_ts: datetime | None = None
    for event in iter_jsonl_objects(session_log_path):
        if event.get("type") != "rate_limit_event":
            continue
        info_raw = event.get("rate_limit_info", {})
        if not isinstance(info_raw, dict):
            continue
        kind_raw = info_raw.get("rateLimitType")
        if kind_raw not in ("one_hour", "five_hour", "seven_day"):
            continue
        ts = _parse_ts(event.get("ts"))
        if ts is not None and (latest_ts is None or ts > latest_ts):
            latest_ts = ts
        new_window = _build_rate_limit_window(kind_raw, info_raw)
        existing = windows.get(kind_raw)
        if existing is None:
            windows[kind_raw] = (new_window, ts)
            continue
        existing_ts = existing[1]
        # Newer ts wins; when ts is missing on either side fall back
        # to last-write-wins so file order still reflects emission
        # order within one session.
        if (
            (ts is not None and existing_ts is not None and ts > existing_ts)
            or ts is None
            or existing_ts is None
        ):
            windows[kind_raw] = (new_window, ts)
    return windows, latest_ts


def _build_rate_limit_window(kind: str, info: dict[str, Any]) -> RateLimitWindow:
    util_raw: Any = info.get("utilization")
    util = float(util_raw) if isinstance(util_raw, (int, float)) else None
    resets_raw: Any = info.get("resetsAt")
    resets = int(resets_raw) if isinstance(resets_raw, (int, float)) else None
    status = str(info.get("status", "unknown"))
    # Caller has already filtered ``kind`` to one of the three known
    # ``rateLimitType`` values; the cast lets the literal-typed field
    # accept the runtime str without smearing the surface type.
    return RateLimitWindow(
        kind=cast(RateLimitWindowKind, kind),
        status=status,
        utilization=util,
        resets_at=resets,
    )


# ── Runpool event iteration ────────────────────────────────────


def iter_runpool_events(run_dir: Path) -> Iterator[dict[str, Any]]:
    """Yield raw event dicts from a run's runpool event streams.

    Reads V2 streams under ``.logs/runpool/`` and exact legacy
    streams under ``.logs/`` for unmarked old runs. When *run_dir*
    is a workflow run with child run directories, child scopes are
    included so operator commands see the whole workflow by default.
    Yields events in file order — chronological within each step,
    not globally sorted across steps. Callers that need global
    ordering must sort by the ``ts`` field.

    Silently skips malformed JSON lines (matches today's
    :func:`metaproc.commands.pool._collect_sub_pools` shape) and
    missing files. The aggregator is observability-grade: a partial
    file shouldn't break a status query.
    """
    if not run_dir.exists() or not run_dir.is_dir():
        return
    for scope_dir in _runpool_event_scope_dirs(run_dir):
        for events_file in _runpool_event_files(scope_dir):
            yield from iter_jsonl_objects(events_file)


def _runpool_event_scope_dirs(run_dir: Path) -> list[Path]:
    """Return run-scope directories that may own runpool logs."""
    scopes: list[Path] = [run_dir]
    logs_dirs = sorted(run_dir.rglob(paths_mod.LOGS_DIR))
    for logs_dir in logs_dirs:
        scope = logs_dir.parent
        if scope == run_dir:
            continue
        scopes.append(scope)

    seen: set[Path] = set()
    unique: list[Path] = []
    for scope in scopes:
        try:
            resolved = scope.resolve()
        except OSError:
            resolved = scope
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(scope)
    return unique


def _run_dir_from_runpool_events_path(runpool_events_path: Path) -> Path | None:
    for parent in runpool_events_path.parents:
        if parent.name == paths_mod.LOGS_DIR:
            return parent.parent
    return None


def _iter_jsonl_events(events_file: Path) -> Iterator[dict[str, Any]]:
    events_file = resolve_existing_artifact(events_file)
    if not events_file.is_file():
        return
    yield from iter_jsonl_objects(events_file)


def _runpool_event_files(run_dir: Path) -> list[Path]:
    event_paths: list[Path] = []
    event_paths.append(paths_mod.runpool_events_for_read(run_dir))

    steps_root = paths_mod.runpool_logs_dir(run_dir) / paths_mod.STEPS_SUBDIR
    if steps_root.is_dir():
        event_paths.extend(iter_artifact_paths(steps_root, f"*/{paths_mod.RUNPOOL_EVENTS_FILE}"))

    workers_root = paths_mod.runpool_logs_dir(run_dir) / paths_mod.WORKERS_SUBDIR
    if workers_root.is_dir():
        event_paths.extend(iter_artifact_paths(workers_root, f"*/{paths_mod.RUNPOOL_EVENTS_FILE}"))

    if not paths_mod.is_v2_run_layout(run_dir):
        legacy_steps_root = paths_mod.run_logs_dir(run_dir) / paths_mod.STEPS_SUBDIR
        if legacy_steps_root.is_dir():
            event_paths.extend(
                iter_artifact_paths(legacy_steps_root, f"*/{paths_mod.POOL_EVENTS_FILE}")
            )
        event_paths.extend(
            iter_artifact_paths(
                paths_mod.run_logs_dir(run_dir), f"worker-*/{paths_mod.POOL_EVENTS_FILE}"
            )
        )

    seen: set[Path] = set()
    files: list[Path] = []
    for path in event_paths:
        path = resolve_existing_artifact(path)
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        files.append(path)
    return files


# ── Session-to-label join (the spec's correlation primitive) ──


def attribute_session_to_label(
    session_log_path: Path,
    runpool_events_path: Path | None = None,
    *,
    runpool_events: Iterable[dict[str, Any]] | None = None,
) -> LabelAttribution | None:
    """Resolve which (adapter, label) was leased for a session JSONL.

    Schema-v2 path (preferred): scans for an ``auth_lease_acquired``
    event whose ``session_log_path`` field matches *session_log_path*.
    Returns the unambiguous ``(adapter, label)`` plus the lease and
    matching ``auth_outcome`` records for downstream rendering.

    Schema-v1 fallback: when no v2 event matches, the function
    returns ``via="unknown"`` rather than guessing via a
    timestamp-window heuristic. The spec contemplates a fallback
    ("Documented as legacy-only; not for new correlations") — kept
    out of the first cut to avoid attributing the wrong label to
    the wrong session under retries / parallel starts. A subsequent
    bead can layer the heuristic on if operator demand surfaces.

    Source events come from either *runpool_events_path* (the
    function reads + parses) or the pre-loaded *runpool_events*
    iterable (lets callers reuse one parse pass across many
    sessions).

    Returns ``None`` only when both event sources are empty;
    otherwise always returns a :class:`LabelAttribution` (with
    ``via="unknown"`` if no match).
    """
    target = str(session_log_path)
    events_iter: Iterable[dict[str, Any]]
    if runpool_events is not None:
        events_iter = runpool_events
    elif runpool_events_path is not None and runpool_events_path.exists():
        run_dir = _run_dir_from_runpool_events_path(runpool_events_path)
        events_iter = (
            iter_runpool_events(run_dir)
            if run_dir is not None
            else _iter_jsonl_events(runpool_events_path)
        )
    else:
        return None

    matching_lease: dict[str, Any] | None = None
    matching_outcome: dict[str, Any] | None = None
    for ev in events_iter:
        if ev.get("event") == "auth_lease_acquired" and ev.get("session_log_path") == target:
            matching_lease = ev
        elif ev.get("event") == "auth_outcome" and ev.get("session_log_path") == target:
            matching_outcome = ev
        if matching_lease is not None and matching_outcome is not None:
            break

    if matching_lease is not None:
        return LabelAttribution(
            adapter=str(matching_lease.get("adapter", "")),
            label=str(matching_lease.get("label", "")),
            via="primary_key",
            lease_event=matching_lease,
            outcome_event=matching_outcome,
        )
    if matching_outcome is not None:
        # Schema-v2 outcome but no matching lease event (e.g. lease
        # event was lost or the lease pre-dates v2 emission). The
        # outcome's own join keys are still authoritative.
        return LabelAttribution(
            adapter=str(matching_outcome.get("adapter", "")),
            label=str(matching_outcome.get("label", "")),
            via="primary_key",
            lease_event=None,
            outcome_event=matching_outcome,
        )
    return LabelAttribution(adapter="", label="", via="unknown")


# ── Aggregator entrypoints ─────────────────────────────────────


def aggregate_label_usage_for_run(
    run_dir: Path,
    *,
    backend: PoolBackend | None = None,
    adapter: str | None = None,
) -> dict[tuple[str, str], LabelUsage]:
    """Aggregate auth-pool usage signals for a single run.

    Combines ``auth_outcome`` aggregation (per-label invocation
    counts, ok/failed split, known-bug distribution) with the
    most-recent ``rate_limit_event`` per label (looked up via the
    join keys from ``auth_lease_acquired``) and (when *backend* is
    provided) pool state.

    Returns a ``{(adapter, label): LabelUsage}`` dict covering every
    label observed in the run's events plus every label in the pool
    when *backend* is provided. *adapter* filters both sources (None
    = include all adapters).
    """
    events = list(iter_runpool_events(run_dir))

    # Per-(adapter, label) outcome aggregation.
    outcome_stats: dict[tuple[str, str], _OutcomeStats] = {}
    # session_log_path → (adapter, label) reverse index from
    # auth_lease_acquired, used to attribute per-session JSONL
    # rate_limit_events back to their label.
    session_to_label: dict[str, tuple[str, str]] = {}
    # (adapter, label) → set of session_log_path strings so we can
    # walk per-session JSONLs once per label.
    label_sessions: dict[tuple[str, str], set[str]] = {}
    for ev in events:
        ev_type = ev.get("event")
        adp = str(ev.get("adapter", ""))
        lbl = str(ev.get("label", ""))
        if not adp or not lbl:
            continue
        if adapter is not None and adp != adapter:
            continue
        key = (adp, lbl)
        if ev_type == "auth_outcome":
            outcome_stats.setdefault(key, _OutcomeStats()).add(ev)
        if ev_type == "auth_lease_acquired":
            session = str(ev.get("session_log_path", "") or "")
            if session:
                session_to_label[session] = key
                label_sessions.setdefault(key, set()).add(session)

    # Per-label rate-limit windows merged across sessions by
    # **per-window timestamp**. Walking sessions in arbitrary order
    # is fine because we keep only the newest record for each window
    # kind based on its own ``ts``. last_event_ts is the freshness
    # proxy on LabelUsage — it doesn't drive selection.
    label_rate_limits: dict[
        tuple[str, str], tuple[dict[str, RateLimitWindow], datetime | None]
    ] = {}
    for key, sessions in label_sessions.items():
        agg_per_window: dict[str, tuple[RateLimitWindow, datetime | None]] = {}
        agg_latest: datetime | None = None
        for session in sessions:
            windows_with_ts, latest = _parse_session_rate_limits(Path(session))
            for kind, (win, ts) in windows_with_ts.items():
                existing = agg_per_window.get(kind)
                if existing is None:
                    agg_per_window[kind] = (win, ts)
                    continue
                existing_ts = existing[1]
                # Strictly-newer ts wins. When either side has a
                # missing ts we keep the existing record — the parser
                # already resolved within-file order, and
                # cross-session resolution without timestamps is
                # ambiguous (refuse to guess).
                if ts is not None and existing_ts is not None and ts > existing_ts:
                    agg_per_window[kind] = (win, ts)
            if latest is not None and (agg_latest is None or latest > agg_latest):
                agg_latest = latest
        agg_windows = {kind: win for kind, (win, _ts) in agg_per_window.items()}
        label_rate_limits[key] = (agg_windows, agg_latest)

    # Pool state: every label in the pool, plus state for labels
    # already in outcome_stats (so an "unused but present" label
    # still surfaces with pool-state info).
    pool_entries: dict[tuple[str, str], PoolEntry] = {}
    if backend is not None:
        for entry in backend.list_entries(adapter=adapter):
            if adapter is not None and entry.adapter != adapter:
                continue
            pool_entries[(entry.adapter, entry.label)] = entry

    all_keys = set(outcome_stats) | set(label_sessions) | set(pool_entries)
    out: dict[tuple[str, str], LabelUsage] = {}
    for key in sorted(all_keys):
        adp, lbl = key
        stats = outcome_stats.get(key) or _OutcomeStats()
        windows, last_event_ts = label_rate_limits.get(key, ({}, None))
        entry = pool_entries.get(key)
        out[key] = _build_label_usage(
            adp,
            lbl,
            stats=stats,
            entry=entry,
            windows=windows,
            last_event_ts=last_event_ts,
        )
    return out


def _build_label_usage(
    adapter: str,
    label: str,
    *,
    stats: _OutcomeStats,
    entry: PoolEntry | None,
    windows: dict[str, RateLimitWindow],
    last_event_ts: datetime | None,
) -> LabelUsage:
    if entry is not None:
        state = entry.state
        pool_state: PoolStateLiteral = state.status  # type: ignore[assignment]
        vehicle = str(getattr(state, "vehicle", "")) if hasattr(state, "vehicle") else ""
        fingerprint = state.fp
        cooling_until = (
            datetime.fromtimestamp(state.cooling_until_ts, tz=UTC)
            if state.cooling_until_ts
            else None
        )
        last_ok_ts = datetime.fromtimestamp(state.last_ok_ts, tz=UTC) if state.last_ok_ts else None
        last_quota_ts = (
            datetime.fromtimestamp(state.last_quota_ts, tz=UTC) if state.last_quota_ts else None
        )
    else:
        pool_state = "unknown"
        vehicle = ""
        fingerprint = ""
        cooling_until = None
        last_ok_ts = None
        last_quota_ts = None

    return LabelUsage(
        adapter=adapter,
        label=label,
        vehicle=vehicle,
        fingerprint=fingerprint,
        pool_state=pool_state,
        cooling_until=cooling_until,
        last_ok_ts=last_ok_ts,
        last_quota_ts=last_quota_ts,
        one_hour=windows.get("one_hour"),
        five_hour=windows.get("five_hour"),
        seven_day=windows.get("seven_day"),
        last_event_ts=last_event_ts,
        invocations_total=stats.total,
        invocations_ok=stats.ok,
        invocations_failed=stats.failed,
        known_bugs=dict(stats.known_bugs),
        last_used_ts=stats.last_used_ts,
        last_run_id=stats.last_run_id,
        probe_state=None,
        probe_request_id=None,
        probe_age_s=None,
        inconsistencies=[],
    )


# ── Cross-source consistency rules (used by `metaproc auth doctor`) ──


def detect_inconsistencies(usage: LabelUsage) -> list[str]:
    """Return human-readable inconsistency hints for a single label.

    Pure function over a built :class:`LabelUsage` — no I/O. Centralizes
    the rules so ``auth doctor`` and any future linter share them.
    """
    issues: list[str] = []
    # Pool says cooling but its 5h/7d windows don't show recent
    # rate-limit activity. Likely stale cooling state.
    if (
        usage.pool_state == "cooling"
        and (usage.five_hour is None or usage.five_hour.utilization is None)
        and (usage.seven_day is None or usage.seven_day.utilization is None)
    ):
        issues.append(
            "pool state=cooling but no recent rate_limit_event in session JSONLs; "
            "cooling state may be stale (re-run with --probe to verify)"
        )
    # Pool says active but JSONL shows 5h utilization at 100%. That's
    # the classic "pool didn't see the most recent 429 yet" hazard.
    if (
        usage.pool_state == "active"
        and usage.five_hour is not None
        and usage.five_hour.utilization is not None
        and usage.five_hour.utilization >= 1.0
    ):
        issues.append(
            "pool state=active but five_hour utilization=1.0 in session JSONLs; "
            "the next acquisition will hit a 429 — re-run with --probe to update state"
        )
    # Label included in dispatches recently but never picked. Strong
    # P0-10 signal; the verifiability hook surfaces this in real time
    # via auth_lease_acquired counts.
    if usage.invocations_total == 0 and usage.pool_state == "active":
        issues.append(
            "label active but 0 invocations across the run's events; "
            "verify load-balancing (priority-order may be selecting a sibling first)"
        )
    return issues


def query_label_headroom(
    adapter: str,
    label: str,
    runs_dir: Path,
    *,
    max_runs: int = 5,
) -> tuple[RateLimitWindow | None, RateLimitWindow | None]:
    """Find the most-recent (5h, 7d) rate-limit windows for one label.

    Walks up to *max_runs* recently-modified run directories under
    *runs_dir* (newest first), aggregates label usage in each, and
    returns the most-recent window per kind for the requested
    ``(adapter, label)``. Returns ``(None, None)`` when no recent
    rate-limit event for the label can be found — operationally
    common for fresh labels.

    Used by :func:`metaproc.adapters.claude_code.ClaudeCodeCliAdapter.
    query_quota_usage` so the existing
    :mod:`metaproc.dispatch.preflight` ``summarize_headroom`` lights
    up without a native Anthropic quota endpoint.

    *max_runs* defaults to 5 — enough to bridge the typical 5h /
    7d reset window without scanning the entire RUNS_DIR.
    """
    if not runs_dir.exists() or not runs_dir.is_dir():
        return None, None
    # Sorted by mtime descending — newest runs first.
    candidates: list[Path] = []
    for child in runs_dir.iterdir():
        if not child.is_dir():
            continue
        try:
            candidates.append(child)
        except OSError:
            continue
    candidates.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)

    five_hour: RateLimitWindow | None = None
    seven_day: RateLimitWindow | None = None
    seen = 0
    # Runs are sorted by mtime descending, and
    # ``aggregate_label_usage_for_run`` already merges
    # ``rate_limit_event`` records within a run by per-window
    # timestamp (see _parse_session_rate_limits). So the first
    # non-None window we encounter walking newest run → oldest is
    # the freshest record we have for that kind. This was the
    # upstream change review finding: previously this loop compared
    # ``u.last_event_ts`` for 5h and 7d separately even though that
    # single ts may belong to a different window.
    for run_dir in candidates:
        if seen >= max_runs:
            break
        if five_hour is not None and seven_day is not None:
            break
        seen += 1
        try:
            usage = aggregate_label_usage_for_run(run_dir, adapter=adapter)
        except Exception:  # noqa: BLE001 — observability path; never raise
            continue
        u = usage.get((adapter, label))
        if u is None:
            continue
        if five_hour is None and u.five_hour is not None:
            five_hour = u.five_hour
        if seven_day is None and u.seven_day is not None:
            seven_day = u.seven_day
    return five_hour, seven_day


__all__ = [
    "LabelAttribution",
    "LabelUsage",
    "RateLimitWindow",
    "aggregate_label_usage_for_run",
    "attribute_session_to_label",
    "detect_inconsistencies",
    "iter_runpool_events",
    "query_label_headroom",
]
