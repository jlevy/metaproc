"""metaproc run-parallel — launch a fan-out step for all items."""

from __future__ import annotations

import asyncio
import heapq
import json
import logging
import os
import shlex
import shutil
import subprocess
import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import typer
from prettyfmt import fmt_timedelta
from strif import atomic_output_file

from metaproc.adapters.base import FailureSeverity
from metaproc.cloud.gcp.resolve_token import resolve_gcp_token
from metaproc.dispatch.credential_pool import (
    FallbackPolicy,
    SelectionPolicy,
    SelectionStrategy,
    gcp_backend,
    local_backend,
)
from metaproc.dispatch.pool_dispatch import PoolDispatchConfig
from metaproc.dispatch.preflight import validate_guard_posture
from metaproc.dispatch.slot_coordinator import SlotCoordinator
from metaproc.runpool.pool import resolve_min_concurrency
from metaproc.settings import POOL_MIN_CONCURRENCY

log = logging.getLogger(__name__)

_QUOTA_RESET_BUFFER_S = 30.0
"""Padding added to the parsed quota reset time before re-submitting the
item. Matches the buffer used by `RunPool.pause_for_quota` so retries
don't race the reset window."""

_POOL_FILL_POLL_INTERVAL_S = 5.0
"""How often the orchestrator wakes to fill capacity after adaptive ramp-up."""


from metaproc import paths as paths_mod
from metaproc.adapters.base import Adapter, AuthFailureClassification
from metaproc.adapters.billing import resolve_billing_class
from metaproc.adapters.registry import (
    ADAPTER_REGISTRY,
    adapter_provider,
    adapter_trace_source,
    derive_variant,
    get_adapter,
)
from metaproc.agent_errors import parse_quota_reset_at
from metaproc.cli import app, get_output
from metaproc.commands.helpers import (
    enforce_no_unresolved_placeholders,
    find_step_def,
    load_item_contexts,
    load_process_spec,
    parse_adapter_config,
    parse_var_args,
    require_runtime_runs_dir,
    resolve_process_path,
    resolve_record_output_paths,
    seed_runtime_vars,
)
from metaproc.config.env_vars import MetaprocEnv
from metaproc.engine.build_plan import build_plan, merge_defaults
from metaproc.engine.code_handler import resolve_code_handler
from metaproc.engine.dep_state import fingerprint_step
from metaproc.engine.discovery import normalize_item_fields
from metaproc.engine.pathing import compute_run_dir, compute_task_state_dir
from metaproc.engine.placeholders import (
    collect_step_runtime_placeholders,
    resolve_runtime_config,
    resolve_templates,
)
from metaproc.engine.preflight import run_preflight
from metaproc.engine.process_scope import expand_process_vars
from metaproc.engine.retry import (
    FailureClass,
    RetryVerdict,
    classify_error,
    classify_failure,
    compute_backoff,
    extract_log_error,
    max_retries_for,
)
from metaproc.engine.run_id import generate_run_id
from metaproc.engine.runtime import (
    prepare_step,
    resolve_batch_size,
    validate_step_inputs_exist,
)
from metaproc.engine.validation import validate_item_outputs
from metaproc.engine.yaml_repair import repair_frontmatter_file
from metaproc.errors import CLIError, ValidationError
from metaproc.io.claimed_items import claim_item
from metaproc.io.state_io import (
    compute_item_dir,
    mark_completed_at,
    mark_failed_at,
    mark_failed_synthetic_at,
    mark_running_at,
    read_status_at,
    reconcile_stale_running,
    write_attempt_at,
    write_result_at,
)
from metaproc.logutil.compaction import try_compact_log
from metaproc.models.authored import IOSpec, ProcessDefaults, ProcessSpec, ProcessStep, RetryPolicy
from metaproc.models.runtime import AttemptRecord, ResultRecord
from metaproc.runpool.backend import LaunchBackend, PreparedLaunch
from metaproc.runpool.pool import (
    ProcessConfig,
    RunPool,
    RunPoolConfig,
    resolve_estimated_process_rss_bytes,
    resolve_host_max_concurrency,
    resolve_initial_memory_budget_fraction,
)
from metaproc.runpool.registry import get_backend
from metaproc.settings import POOL_STALL_TIMEOUT_MINUTES


def _auth_forces_abort(classification: AuthFailureClassification | None) -> bool:
    """Return True when a pool auth classification demands skipping retry.

    Plan §Phase 5: the pool adapter's ``effective_severity()`` takes
    precedence over the generic retry classifier. ABORT means retrying
    won't help — dead refresh token, expired credential, matched known
    bug — so the dispatch must fail fast instead of burning attempts.

    ``None`` (non-pool dispatch or success teardown) returns ``False``
    so the generic retry path is untouched.
    """

    if classification is None:
        return False
    return classification.effective_severity() == FailureSeverity.ABORT


def _compute_pool_cooling_delay(
    pool_dispatch: Any | None,
    *,
    floor_s: float = 60.0,
    ceiling_s: float = 30 * 60.0,
    contention_s: float = 60.0,
    jitter_s: float = 30.0,
) -> float:
    """Return seconds to wait before re-attempting an item that hit pool exhaustion.

    When ``acquire_slot`` raises ``PoolSlotUnavailableError``, distinguish two
    failure modes and pick a delay matched to each:

    1. **Cooling exhaustion** — at least one entry has
       ``cooling_until_ts``. Wait for the earliest reset (capped to
       ``ceiling_s``), since the bucket is genuinely throttled.
    2. **Lease contention** — entries are healthy but other items hold
       all the leases. The right wait is "until any in-flight item
       releases its slot", which is short. Default ``contention_s = 60s``
       so the queue keeps draining as items finish.

    Without this distinction, a fan-out with 10 items and 2 labels would
    stall items 3-10 by ``ceiling_s`` each (30 min) under the cooling
    interpretation, even though the labels are healthy. Surfaced by the
    Tuesday 2026-04-28 smoke (mine-adhoc step) — see plan progress log.

    Returns ``ceiling_s`` defensively when the pool peek fails.
    """
    if pool_dispatch is None:
        return ceiling_s
    try:
        backend = pool_dispatch.coordinator.backend
        entries = backend.list_entries(adapter=pool_dispatch.adapter)
        now = time.time()
        earliest: float | None = None
        any_cooling_no_ts = False
        for entry in entries:
            if entry.state.status == "cooling":
                ts = entry.state.cooling_until_ts
                if ts is None:
                    any_cooling_no_ts = True
                    continue
                ts_float = float(ts)
                if earliest is None or ts_float < earliest:
                    earliest = ts_float
        if earliest is not None:
            wait_s = max(0.0, earliest - now) + jitter_s
            return max(floor_s, min(ceiling_s, wait_s))
        if any_cooling_no_ts:
            # "Cooling indefinitely" — Anthropic 429s often omit Retry-After
            # for the 5h Pro window, so cooling_until_ts is None. Use the
            # ceiling so the orchestrator sleeps a meaningful interval and
            # re-probes (rather than the lease-contention 60s default,
            # which thrashes against a known-throttled pool).
            return ceiling_s
        # Lease contention: labels are healthy, just all leased. Re-probe
        # quickly so the freed-slot pickup is fast.
        return contention_s
    except Exception:  # noqa: BLE001 — pool peek must never crash retry path
        log.debug("pool_cooling_delay: pool peek failed; falling back to ceiling", exc_info=True)
        return ceiling_s


def _expand_pool_exclude_by_quota_group(
    *,
    pool_dispatch: Any,
    lease: Any,
    exclude_list: list[tuple[str, str]],
) -> None:
    """Add every sibling label sharing the failing label's quota_group to
    *exclude_list*.

    Phase 4: on a 429 cooling classification, the next
    retry should skip the entire quota group, not just the failing
    label. Otherwise a same-org alt label gets a wasted 429 in lockstep.

    Treats unknown-quota-group labels conservatively — if the failing
    label's group is ``unknown``, we don't expand (we don't know what
    else shares quota). Operators can resolve this by setting
    ``--quota-group org:<uuid>`` or ``account:<hex>`` on push.

    Logging is intentionally minimal — at-debug-level only — because
    quota-group expansion can fire many times per cohort. Operators
    inspect the audit trail via the auth_outcome event's
    ``quota_group_*`` fields, not via dispatch logs.
    """
    try:
        backend = pool_dispatch.coordinator.backend
        failing_entry = backend.get_entry(lease.adapter, lease.label)
    except (KeyError, AttributeError):
        # Backend doesn't have this entry (rare race) or coordinator is
        # an older API shape — fall back to single-label exclude
        # (existing behavior).
        return
    failing_group = failing_entry.state.quota_group
    if failing_group.kind == "unknown":
        return
    # Sibling lookup: list all entries for this adapter, exclude the
    # ones not in the same group, and add (adapter, label) pairs to
    # the exclude list. The failing label is already there.
    try:
        all_entries = backend.list_entries(adapter=lease.adapter)
    except Exception:  # noqa: BLE001 — defensive: a backend hiccup must not block teardown
        return
    already_excluded = set(exclude_list)
    for entry in all_entries:
        if (entry.adapter, entry.label) in already_excluded:
            continue
        if entry.state.quota_group == failing_group:
            exclude_list.append((entry.adapter, entry.label))
            already_excluded.add((entry.adapter, entry.label))


def _teardown_pool_slot(
    *,
    pool_dispatch: Any | None,
    pool: Any,
    shared: dict[str, Any],
    error_str: str | None,
) -> AuthFailureClassification | None:
    """Tear down a leased pool slot and emit the auth_outcome event.

    Plan §Phase 5 — the outcome event is the operator-facing
    aggregation surface for credential health, known-bug reoccurrence,
    and rotation. Emitting from this one seam guarantees every
    pool-dispatched item writes exactly one ``auth_outcome`` whether
    it succeeded, was retried, or failed permanently.

    Extracted as a module-level function so the integration test can
    exercise the full chain (classify → teardown → record) without
    reaching into the run-parallel inner closures.

    ``None`` returned when no slot was leased (non-pool dispatch or a
    prior teardown already released it). Otherwise returns the
    :class:`AuthFailureClassification` the coordinator received so the
    caller can consult :func:`_auth_forces_abort` for retry decisions.
    """
    if pool_dispatch is None:
        return None
    lease = shared.pop("slot_lease", None)
    if lease is None:
        return None

    # Deferred so tests can monkeypatch
    # metaproc.dispatch.pool_dispatch.{classify_failure_for_slot,build_auth_outcome}
    # at module attribute level; a top-level binding would capture the
    # pre-patch function and bypass the monkeypatch.
    from metaproc.dispatch.pool_dispatch import (  # noqa: PLC0415 -- test monkeypatch boundary
        build_auth_outcome,
        classify_failure_for_slot,
    )

    classification: AuthFailureClassification | None = None
    session_log: Path | None = None
    if isinstance(shared.get("log_path"), Path):
        session_log = shared["log_path"]
    if error_str is not None:
        classification = classify_failure_for_slot(
            lease,
            error_str=error_str,
            session_log_path=session_log,
        )
        # Only accumulate the failed label for retry's fallback walk when the
        # classification indicates a *label-level* problem (``cooling`` =
        # rate-limited, ``expired`` = credential expired). Content-level
        # failures (``invalid_outputs``, malformed YAML, runbook crashes)
        # classify as ``unknown`` because they are not a credential issue
        # (see ``claude_code.py:classify_failure`` ``status="unknown"`` path).
        # Excluding the label on ``unknown`` is wrong: the label is healthy,
        # the *output* was bad. Two consecutive ``unknown`` failures used to
        # exhaust the per-item label set permanently, sending the worker
        # into a "pool exhausted — waiting 60s for slot recovery" loop with
        # no escape.
        exclude_list = shared.setdefault("pool_exclude", [])
        if classification.status in ("cooling", "expired"):
            exclude_list.append((lease.adapter, lease.label))
        # ── Phase 4: quota-group walk on 429 cooling ──
        # When the failure is rate-limit cooling AND the failing label
        # has a known quota_group (org or account), exclude every
        # sibling label in the same group from this retry. Anthropic
        # rate-limits at account level (and per organizationUuid in
        # some configurations — claude-code#41886), so failing over
        # from alt1 (org=ABC) to alt2 (also org=ABC) just hits 429
        # again on the next call. Walking past the whole group
        # collapses N×429 retries down to one.
        if classification.status == "cooling" and getattr(pool_dispatch, "cross_quota_group", True):
            _expand_pool_exclude_by_quota_group(
                pool_dispatch=pool_dispatch,
                lease=lease,
                exclude_list=exclude_list,
            )
    # Preserve adapter-declared diagnostic logs (claude-code-debug.log,
    # …) next to the session log under the run's standard ``.logs/``
    # tree before teardown rmtree's the slot. Always — both success
    # and failure runs benefit from having API-level debug output
    # available next to the captured stream-json for correlation.
    if session_log is not None:
        pool_dispatch.coordinator.preserve_diagnostics(lease, session_log)
    flushed_blob = pool_dispatch.coordinator.teardown(lease, failure=classification)

    # attempt_number is 1-indexed, so the retries *already burned*
    # before this attempt ran is attempt_number - 1.
    retry_count = max(0, int(shared.get("attempt_number", 1)) - 1)
    outcome = build_auth_outcome(
        lease,
        classification=classification,
        flushed_blob=flushed_blob,
        retry_count=retry_count,
        fallback_policy=pool_dispatch.fallback_policy,
    )
    pool.record_auth_outcome(asdict(outcome))
    return classification


def _resolve_env(target_env: dict[str, str] | None, item_vars: dict[str, str]) -> dict[str, str]:
    """Resolve per-item templates in env values (e.g. ``{{VARIANT}}``, ``{{EVENT_ID}}``)."""
    if not target_env:
        return {}
    resolved: dict[str, str] = {}
    for key, value in target_env.items():
        resolved[key] = resolve_templates(value, item_vars)
    from metaproc.plugins.discovery import (  # noqa: PLC0415 -- avoid CLI import cycle
        get_plugin_registry,
    )

    for transform in get_plugin_registry().execution_env_transforms:
        resolved = transform(resolved, item_vars)
    return resolved


def _build_pool_dispatch_template(
    *,
    auth_account: str | None,
    auth_backend: str | None,
    auth_fallback_policy: str,
    auth_include_labels: list[str],
    auth_exclude_labels: list[str],
    auth_cross_quota_group: bool,
    backend_name: str,
    run_context: str,
    out: Any,
    auth_policy: str | None = None,
) -> Any | None:
    """Construct a :class:`PoolDispatchConfig` from CLI flags, or ``None``.

    Mirrors the construction in
    :func:`metaproc.commands.run_process.run_process_command`. Returns
    ``None`` when ``auth_account`` is empty — keeping the legacy
    single-credential bootstrap path intact for callers who don't opt
    into the pool. Workers do NOT run the pre-fan-out probe gate
    (run-process already did that once at orchestrator time and
    persisted any state transitions to the shared SM-backed pool).

    Cloud workers must receive the same ``--auth-*`` flags and use the same pool
    dispatch path as local ``run-process``.
    """
    if not auth_account:
        return None
    if auth_include_labels and auth_exclude_labels:
        raise CLIError("--auth-include-labels and --auth-exclude-labels are mutually exclusive.")
    # Default backend matches the dispatch backend so cloud workers
    # read from Secret Manager and local runs read from ~/.metaproc/.
    resolved_auth_backend = (
        auth_backend
        if auth_backend is not None
        else ("gcp-secret-manager" if backend_name == "gcp-worker" else "local")
    )

    if resolved_auth_backend == "gcp-secret-manager":
        project = MetaprocEnv.METAPROC_GCP_PROJECT.read_str(default="")
        if not project:
            raise CLIError(
                "METAPROC_GCP_PROJECT is not set; required for --auth-backend "
                "gcp-secret-manager. Export it or use --auth-backend local."
            )
        pool_user = MetaprocEnv.METAPROC_AUTH_POOL.read_str(
            default=""
        ) or MetaprocEnv.USER.read_str(default="unknown")
        auth_pool_backend_obj = gcp_backend(project, pool_user=pool_user)
    elif resolved_auth_backend == "local":
        auth_pool_backend_obj = local_backend()
    else:
        raise CLIError(
            f"--auth-backend must be 'local' or 'gcp-secret-manager', "
            f"got {resolved_auth_backend!r}."
        )

    # Default policy switching (plan-2026-05-03 Goal #1):
    # ROUND_ROBIN is the dispatch default when --auth-include-labels has
    # ≥ 2 labels and --auth-policy is unset. PRIORITY_ORDER reproduces
    # the legacy behavior bit-for-bit when explicitly requested.
    if auth_policy:
        try:
            resolved_policy = SelectionPolicy(auth_policy)
        except ValueError as exc:
            raise CLIError(
                f"--auth-policy must be one of "
                f"priority-order|round-robin|least-active, got {auth_policy!r}."
            ) from exc
    elif len(auth_include_labels) >= 2:
        resolved_policy = SelectionPolicy.ROUND_ROBIN
    else:
        resolved_policy = SelectionPolicy.PRIORITY_ORDER
    strategy = SelectionStrategy(
        policy=resolved_policy,
        labels=tuple(auth_include_labels),
    )
    excluded_pairs = tuple((auth_account, lbl) for lbl in auth_exclude_labels)
    try:
        fallback_policy_enum = FallbackPolicy(auth_fallback_policy)
    except ValueError as exc:
        raise CLIError(
            f"--auth-fallback-policy must be one of "
            f"none|same-provider|cross-provider|both, got {auth_fallback_policy!r}."
        ) from exc
    coordinator = SlotCoordinator(auth_pool_backend_obj)
    runs_dir_env = MetaprocEnv.RUNS_DIR.read_str(default="")
    runs_dir_for_pool = Path(runs_dir_env) if runs_dir_env else Path()
    template = PoolDispatchConfig(
        coordinator=coordinator,
        adapter=auth_account,
        runs_dir=runs_dir_for_pool,
        run_id=run_context,
        step="",  # filled by run-parallel's _run_agent_pool callers
        strategy=strategy,
        fallback_policy=fallback_policy_enum,
        exclude=excluded_pairs,
        cross_quota_group=auth_cross_quota_group,
    )
    priority_repr = list(strategy.labels) or "(any active)"
    out.progress(
        f"Auth pool: adapter={auth_account} backend={resolved_auth_backend} "
        f"strategy={resolved_policy.value} labels={priority_repr} "
        f"fallback={fallback_policy_enum.value}"
    )
    return template


def _resolve_retry_policy(
    step_def: ProcessStep,
    defaults: ProcessDefaults,
    *,
    max_retries_override: int | None = None,
    no_retry: bool = False,
) -> RetryPolicy:
    """Resolve the effective retry policy from CLI flags and spec.

    Priority: CLI flags > step for_each.retry > defaults.retry > RetryPolicy().
    """
    if no_retry:
        return RetryPolicy(max_retries=0)

    # Step-level retry from for_each
    step_retry = step_def.for_each.retry if step_def.for_each else None

    # Process-level default
    default_retry = defaults.retry

    # Pick the most specific spec-level policy
    policy = step_retry or default_retry or RetryPolicy()

    # CLI override for max_retries
    if max_retries_override is not None:
        policy = policy.model_copy(update={"max_retries": max_retries_override})

    return policy


@app.command("run-parallel")
def run_parallel(
    process_spec: Path = typer.Argument(..., help="Path to .process.md spec file"),
    step: str = typer.Option(..., "--step", help="Step ID to run"),
    var: list[str] = typer.Option([], "--var", help="KEY=VALUE parameters"),
    items: str | None = typer.Option(None, "--items", help="Comma-separated list of values"),  # noqa: UP007
    batch_size: int | None = typer.Option(None, "--batch-size", help="Override batch size"),  # noqa: UP007
    adapter: str | None = typer.Option(None, "--adapter", help="Override adapter type"),  # noqa: UP007
    adapter_config: list[str] = typer.Option(
        [], "--adapter-config", help="KEY=VALUE adapter config overrides"
    ),
    variant_flag: str | None = typer.Option(None, "--variant", help="Variant directory name"),  # noqa: UP007
    dry_run: bool = typer.Option(False, "--dry-run", help="Print launch plan without spawning"),
    max_retries: int | None = typer.Option(
        None, "--max-retries", help="Override max retry attempts for failed items"
    ),  # noqa: UP007
    no_retry: bool = typer.Option(
        False, "--no-retry", help="Disable retries even if spec defines a retry policy"
    ),
    limit: int | None = typer.Option(
        None, "--limit", help="Max number of items to process (for pre-checks)"
    ),  # noqa: UP007
    backend: str = typer.Option("local", "--backend", help="Launch backend (e.g. local, mock)"),
    max_concurrency: int | None = typer.Option(
        None, "--max-concurrency", help="Max concurrent processes (pool mode)"
    ),  # noqa: UP007
    initial_concurrency: int | None = typer.Option(
        None,
        "--initial-concurrency",
        help="Starting concurrency; ramps up to max via pressure (pool mode)",
    ),  # noqa: UP007
    min_concurrency: int | None = typer.Option(
        None, "--min-concurrency", help="Min concurrent processes under pressure (pool mode)"
    ),  # noqa: UP007
    run_title: str | None = typer.Option(
        None, "--run-title", help="Human-readable title for auto-generated RUN_ID"
    ),  # noqa: UP007
    auth_account: str | None = typer.Option(  # noqa: UP007
        None,
        "--auth-account",
        help=(
            "Adapter type for auth-pool dispatch (e.g. claude-code-cli). "
            "When set, run-parallel uses the labeled credential pool instead of "
            "the legacy single-credential bootstrap, leasing an eligible label "
            "per --auth-fallback-policy. Mirrors the run-process surface; needed "
            "here so the cloud worker entrypoint can construct the same "
            "PoolDispatchConfig as a local dispatch."
        ),
    ),
    auth_backend: str | None = typer.Option(  # noqa: UP007
        None,
        "--auth-backend",
        help="Auth pool backend: 'local' or 'gcp-secret-manager' (default depends on --backend).",
    ),
    auth_fallback_policy: str = typer.Option(
        "none",
        "--auth-fallback-policy",
        help="Cross-adapter fallback: none|same-provider|cross-provider|both.",
    ),
    auth_policy: str | None = typer.Option(  # noqa: UP007 — typer-friendly Optional
        None,
        "--auth-policy",
        help=(
            "Selection policy: priority-order|round-robin|least-active. "
            "Defaults to round-robin when --auth-include-labels has ≥ 2 "
            "labels (plan-2026-05-03), else priority-order. Pass "
            "--auth-policy priority-order explicitly to reproduce legacy "
            "first-eligible behavior bit-for-bit."
        ),
    ),
    auth_include_labels: list[str] = typer.Option(
        [],
        "--auth-include-labels",
        help=(
            "Priority-ordered allowed labels; mutually exclusive with "
            "--auth-exclude-labels. Repeat for each label."
        ),
    ),
    auth_exclude_labels: list[str] = typer.Option(
        [],
        "--auth-exclude-labels",
        help="Operator-level pin-outs for the entire dispatch.",
    ),
    auth_cross_quota_group: bool = typer.Option(
        True,
        "--auth-cross-quota-group/--no-auth-cross-quota-group",
        help=(
            "Permit cross-quota-group fallback within the auth pool. Defaults "
            "to true. Pass --no-auth-cross-quota-group for diagnostic "
            "dispatches against a single quota group. Mirrors the run-process "
            "surface; needed here so cloud workers honor the operator's "
            "cross-quota opt-out instead of crashing on an unknown option or "
            "silently dropping it."
        ),
    ),
    auth_preflight_quota_guard: str = typer.Option(
        "warn",
        "--auth-preflight-quota-guard",
        help=(
            "Pre-fan-out quota gate posture: off|warn|refuse. Defaults to "
            "warn (compute verdict, log a warning event, always proceed). "
            "refuse blocks the dispatch when projected demand > 80% of "
            "headroom; off skips the gate entirely. plan-2026-05-03 "
            "Phase 3."
        ),
    ),
) -> None:
    """Launch a fan-out step for all items in parallel batches.

    This is a low-level plumbing command. For normal workflow execution,
    use ``run-process`` which walks the full DAG automatically.

    ``run-parallel`` is still useful as: a worker VM entrypoint, a manual
    override for running a single step with fine-grained control, or when
    you need ``--items``, ``--limit``, or ``--no-retry`` flags.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    out = get_output()

    try:
        validate_guard_posture(auth_preflight_quota_guard)
    except ValueError as exc:
        raise CLIError(str(exc)) from exc

    process_path = resolve_process_path(process_spec)
    process_dir = process_path.parent

    spec = load_process_spec(process_path)
    variables = seed_runtime_vars(parse_var_args(var))
    config_overrides = parse_adapter_config(adapter_config)

    # Auto-generate RUN_ID if not explicitly provided and the spec requires it.
    if "RUN_ID" not in variables and spec.requires_param("RUN_ID"):
        variables["RUN_ID"] = generate_run_id(
            process_slug=spec.name,
            title=run_title or "",
        )
        log.info("Auto-generated RUN_ID: %s", variables["RUN_ID"])

    variables = expand_process_vars(spec, variables, process_dir=process_dir)
    require_runtime_runs_dir(variables, command="run-parallel")

    # `--variant` may name an entry in `spec.defaults.adapters` (e.g.
    # `claude-code-cli`) OR be a synthetic composite the engine derives at
    # fan-out time from adapter+model (e.g. `pi-cli-glm-5-maas` for a
    # mine-adhoc step whose spec declares `adapter.type: pi-cli` with
    # config_by_variant.pi-cli.{provider,model}). Mirror run-process: when no
    # explicit `--adapter` is passed and the variant DOES name a known
    # adapter slot, let the variant drive adapter resolution. Otherwise the
    # variant is just a run-dir label and the step's own spec drives adapter
    # selection. Without this branch, a worker invoked with
    # `--variant pi-cli-glm-5-maas` (the cloud worker leg of mine-adhoc) was
    # rejected by build_plan as `unknown adapter override`.
    if adapter:
        effective_adapter_override: str | None = adapter
    elif variant_flag and (
        variant_flag in spec.defaults.adapters or variant_flag in ADAPTER_REGISTRY
    ):
        effective_adapter_override = variant_flag
    else:
        effective_adapter_override = None
    try:
        resolved = build_plan(
            spec,
            variables,
            process_path=process_path,
            adapter_override=effective_adapter_override,
            config_overrides=config_overrides or None,
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError(str(exc)) from exc

    target = next((s for s in resolved.steps if s.step_id == step), None)
    if target is None:
        available = [s.step_id for s in resolved.steps]
        raise CLIError(f"step '{step}' not found. Available: {', '.join(available)}")

    step_def = find_step_def(spec, step)
    merged = merge_defaults(step_def, spec.defaults)
    step_hash = fingerprint_step(target)

    # Infer --each from resolved step's fan_out
    if target.fan_out is not None:
        each = target.fan_out.bind
    else:
        raise CLIError(f"step '{step}' has no 'for_each.bind' field")

    adapter_type = target.adapter.type
    merged_config = dict(target.adapter.config)
    if merged.get("max_budget_usd") is not None:
        merged_config.setdefault("max_budget_usd", merged["max_budget_usd"])
    runtime_config = cast("dict[str, object]", resolve_runtime_config(merged_config, variables))

    derived_variant = derive_variant(adapter_type, runtime_config)
    effective_execution_profile = target.execution_profile or adapter or derived_variant
    effective_variant = (
        target.artifact_namespace
        or step_def.artifact_namespace
        or variant_flag
        or step_def.variant
        or derived_variant
    )
    variables["EXECUTION_PROFILE"] = effective_execution_profile
    variables["ARTIFACT_NAMESPACE"] = effective_variant
    variables["VARIANT"] = effective_variant

    effective_outputs = target.outputs
    effective_batch_size = resolve_batch_size(step_def, batch_size)

    adapter_obj: Adapter | None = None
    if target.mode != "code":
        try:
            adapter_obj = get_adapter(adapter_type)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    # ── Reconcile stale "running" markers from dead pools ─────────
    # If a previous pool was killed, items it marked "running" would be
    # skipped by discovery forever.  Reset them to "failed" so they're
    # retried.
    run_dir = compute_run_dir(spec, variables)
    stale_count = reconcile_stale_running(run_dir)
    if stale_count:
        out.progress(f"Reconciled {stale_count} stale running item(s) from dead pool")

    # Derive items from source if --items not provided
    if items is None:
        from metaproc.engine.discovery import (  # noqa: PLC0415 -- pre-existing local import; needs review
            discover_items_from_source,
        )

        for_each_def = step_def.for_each
        input_name = for_each_def.over if for_each_def else None
        input_spec = target.inputs.get(input_name) if input_name else None
        source = input_spec.path if input_spec else None
        if source:
            resolved_source = resolve_templates(source, variables)
            source_path = Path(resolved_source)
            if not source_path.exists():
                raise CLIError(f"source file not found: {source_path}")
            try:
                discovery = discover_items_from_source(
                    source_path,
                    step_def,
                    output_paths=effective_outputs or None,
                    params=variables,
                    reuse_policy=target.reuse_policy,
                    run_dir=run_dir,
                )
            except (ValueError, TypeError) as exc:
                raise ValidationError(str(exc)) from exc
            item_contexts = discovery.actionable_contexts
            if not item_contexts:
                raise CLIError(f"{source_path}: no actionable items found after filtering")
            if limit is not None and len(item_contexts) > limit:
                item_contexts = item_contexts[:limit]
                out.progress(f"Limited to {limit} items (--limit)")
            item_list = [context[each] for context in item_contexts]
            out.progress(
                f"Discovered {len(item_list)} actionable items from {source_path} "
                f"({len(discovery.filtered_items)} filtered)"
            )
        else:
            raise CLIError(
                f"--items not provided and step '{step}' has no resolved input for 'for_each.over'"
            )
    else:
        item_list = [s.strip() for s in items.split(",") if s.strip()]
        if not item_list:
            raise CLIError("--items is empty")

        item_fields = normalize_item_fields(step_def)
        extra_fields = [field for field in item_fields if field != each]
        if extra_fields:
            # Step requires extra item fields beyond the primary key.
            # Try enrichment sources in order:
            # 1. Worker payload (inline env var or spilled file)
            # 2. Source file from the resolved for_each.over input
            item_set = set(item_list)
            all_contexts = load_item_contexts()

            if all_contexts:
                item_contexts = [ctx for ctx in all_contexts if ctx.get(each) in item_set]
                out.progress(f"Enriched {len(item_contexts)} items from worker payload")
            else:
                from metaproc.engine.discovery import (  # noqa: PLC0415 -- pre-existing local import; needs review
                    discover_items_from_source,
                )

                for_each_def = step_def.for_each
                input_name = for_each_def.over if for_each_def else None
                input_spec = target.inputs.get(input_name) if input_name else None
                source = input_spec.path if input_spec else None
                if not source:
                    raise CLIError(
                        f"step '{step}' requires item fields {', '.join([each, *extra_fields])}; "
                        f"--items only supplies {each} and no resolved input is available for "
                        f"enrichment."
                    )
                resolved_source = resolve_templates(source, variables)
                source_path = Path(resolved_source)
                if not source_path.exists():
                    raise CLIError(f"source file not found: {source_path}")
                discovery = discover_items_from_source(
                    source_path,
                    step_def,
                    output_paths=effective_outputs or None,
                    params=variables,
                    reuse_policy=target.reuse_policy,
                    run_dir=run_dir,
                )
                item_contexts = [
                    ctx for ctx in discovery.actionable_contexts if ctx.get(each) in item_set
                ]
            if not item_contexts:
                raise CLIError(
                    f"none of the --items values found in source after filtering "
                    f"({len(item_set)} requested, 0 matched)"
                )
            if not all_contexts:
                out.progress(
                    f"Enriched {len(item_contexts)} items from source "
                    f"({len(item_set)} requested, {len(item_set) - len(item_contexts)} already completed)"
                )
        else:
            item_contexts = [{each: item} for item in item_list]

    try:
        for item_context in item_contexts:
            item_vars = dict(variables)
            item_vars.update(item_context)
            validate_step_inputs_exist(
                target.inputs,
                item_vars,
                context=f"step '{step}' for {each}={item_context[each]}",
            )
    except ValueError as exc:
        raise CLIError(str(exc)) from exc

    optional_unset = {name for name in spec.optional_input_names if name not in variables}
    allowed_runtime = collect_step_runtime_placeholders(step_def) | optional_unset

    retry_policy = _resolve_retry_policy(
        step_def,
        spec.defaults,
        max_retries_override=max_retries,
        no_retry=no_retry,
    )

    batches = [
        item_contexts[i : i + effective_batch_size]
        for i in range(0, len(item_contexts), effective_batch_size)
    ]
    total = len(item_list)
    n_batches = len(batches)

    retry_info = f", max-retries={retry_policy.max_retries}" if retry_policy.max_retries > 0 else ""
    out.progress(
        f"run-parallel: {total} items, {n_batches} batch{'es' if n_batches != 1 else ''} "
        f"(batch-size={effective_batch_size}{retry_info})"
    )

    # Does the adapter need a GCP/vertex token?
    # Cloud workers skip local token resolution — the container VM has its own
    # service account and resolves tokens via the metadata server.
    _needs_gcloud_token = (
        adapter_type == "pi-cli"
        and str(merged_config.get("provider", "")).startswith("vertex")
        and backend == "local"
    )

    # ── Pre-flight checks ────────────────────────────────────────
    if not dry_run:
        preflight_results = run_preflight(needs_gcloud=_needs_gcloud_token)
        for ok, msg in preflight_results:
            symbol = "✓" if ok else "✗"
            out.progress(f"  {symbol} {msg}")
        failures = [msg for ok, msg in preflight_results if not ok]
        if failures:
            raise CLIError(f"Pre-flight check failed: {'; '.join(failures)}")

    # ── mode: code branch (parallel) ──────────────────────────────
    if target.mode == "code":
        handler_ref = target.handler
        command_ref = target.command

        if not handler_ref and not command_ref:
            raise CLIError(f"step '{step}' is mode:code but has no handler or command")

        handler_fn = None
        process_step = None
        if handler_ref:
            try:
                handler_fn = resolve_code_handler(handler_ref, process_dir)
            except (ValueError, FileNotFoundError, ImportError, AttributeError, TypeError) as exc:
                raise ValidationError(str(exc)) from exc
            process_step = step_def.model_copy(
                deep=True,
                update={"inputs": target.inputs, "outputs": target.outputs},
            )

        if dry_run:
            label = f"Handler: {handler_ref}" if handler_ref else f"Command: {command_ref}"
            out.data(f"=== DRY RUN ===\nMode: code\nVariant: {effective_variant}\n{label}")
            for bi, batch in enumerate(batches, 1):
                out.data(f"\nBatch {bi}/{n_batches}: {', '.join(c[each] for c in batch)}")
            return

        run_context = variables.get("RUN_ID", variables.get("DATE", variables.get("SCOPE", "")))
        run_id = f"{spec.name}/{run_context}"
        all_results: list[tuple[str, int]] = []

        for bi, batch in enumerate(batches, 1):
            batch_items = [context[each] for context in batch]
            out.progress(f"\n=== Batch {bi}/{n_batches}: {', '.join(batch_items)} ===")
            for item_context in batch:
                item = item_context[each]
                item_vars = dict(variables)
                item_vars.update(item_context)
                state_dir = compute_task_state_dir(run_dir, step_def, item_vars)
                artifact_dir = compute_item_dir(effective_outputs, item_vars)

                runtime_info: dict[str, object] = {
                    "mode": "code",
                    "variant": effective_variant,
                }
                if handler_ref:
                    runtime_info["handler"] = handler_ref
                else:
                    runtime_info["command"] = command_ref

                attempt = 1
                while True:
                    state_dir.mkdir(parents=True, exist_ok=True)
                    mark_running_at(
                        state_dir,
                        run_id=run_id,
                        step_id=step,
                        item=dict(item_context),
                        attempt=attempt,
                    )
                    write_attempt_at(
                        state_dir,
                        AttemptRecord(
                            run_id=run_id,
                            step_id=step,
                            item=dict(item_context),
                            params=dict(item_vars),
                            outputs=resolve_record_output_paths(effective_outputs, item_vars),
                            runtime=runtime_info,
                            step_hash=step_hash,
                        ),
                    )

                    error_str: str | None = None
                    try:
                        if handler_fn is not None:
                            assert process_step is not None
                            handler_fn(dict(item_vars), process_step)
                        else:
                            assert command_ref is not None
                            resolved_cmd = resolve_templates(command_ref, item_vars)
                            env = dict(os.environ)
                            env.update(_resolve_env(target.env, item_vars))
                            subprocess.run(
                                shlex.split(resolved_cmd),
                                env=env,
                                cwd=str(process_dir),
                                check=True,
                                capture_output=True,
                                text=True,
                            )
                        exit_code = 0
                    except subprocess.CalledProcessError as exc:
                        error_str = f"command exit code {exc.returncode}"
                        exit_code = 1
                    except Exception as exc:  # noqa: BLE001
                        error_str = str(exc)
                        exit_code = 1

                    if exit_code != 0:
                        assert error_str is not None
                        # Check retry policy before recording final failure
                        verdict = classify_error(error_str)
                        cap = max_retries_for(classify_failure(error_str), retry_policy.max_retries)
                        if verdict == RetryVerdict.RETRY and attempt <= cap:
                            backoff = compute_backoff(attempt, retry_policy)
                            out.progress(
                                f"  {each}={item}: retryable ({error_str}), "
                                f"retry in {backoff:.0f}s (attempt {attempt}/{cap + 1})"
                            )
                            time.sleep(backoff)
                            attempt += 1
                            continue
                        # Permanent failure or retries exhausted
                        out.progress(f"  {each}={item}: failed ({error_str})")
                        mark_failed_at(state_dir, error=error_str)
                        all_results.append((item, 1))
                        break

                    if artifact_dir is not None and effective_outputs:
                        # Attempt YAML repair before validation
                        for io_spec in effective_outputs.values():
                            if io_spec.path:
                                out_file = artifact_dir / Path(io_spec.path).name
                                if out_file.exists() and repair_frontmatter_file(out_file):
                                    out.progress(f"  Repaired YAML in {out_file.name} for {item}")
                        output_errors = validate_item_outputs(
                            artifact_dir, effective_outputs, variables=item_vars
                        )
                        if output_errors:
                            error_str = f"output validation failed: {'; '.join(output_errors)}"
                            verdict = classify_error(error_str)
                            cap = max_retries_for(
                                FailureClass.INVALID_OUTPUT, retry_policy.max_retries
                            )
                            if verdict == RetryVerdict.RETRY and attempt <= cap:
                                backoff = compute_backoff(attempt, retry_policy)
                                out.progress(
                                    f"  {each}={item}: retryable ({error_str}), "
                                    f"retry in {backoff:.0f}s (attempt {attempt}/{cap + 1})"
                                )
                                time.sleep(backoff)
                                attempt += 1
                                continue
                            mark_failed_at(state_dir, error=error_str)
                            exit_code = 1
                        else:
                            mark_completed_at(state_dir)
                            write_result_at(
                                state_dir,
                                ResultRecord(
                                    run_id=run_id,
                                    step_id=step,
                                    state="completed",
                                    validated=True,
                                    outputs=resolve_record_output_paths(
                                        effective_outputs, item_vars
                                    ),
                                    published_at=datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S"),
                                    step_hash=step_hash,
                                ),
                            )
                    else:
                        mark_completed_at(state_dir)
                    all_results.append((item, exit_code))
                    status = "ok" if exit_code == 0 else "failed"
                    out.progress(f"  {each}={item}: {status}")
                    break

        failed = [(item, code) for item, code in all_results if code != 0]
        out.progress(f"\nrun-parallel (code): {len(all_results)} items, {len(failed)} failed")
        raise typer.Exit(code=1 if failed else 0)

    # ── mode: agent branch (parallel) ──────────────────────────────

    # For cloud backends with Filestore NFS, determine the cloud-side RUNS_DIR
    # so template resolution in prompts/output paths uses the NFS mount path
    # instead of the orchestrator's local RUNS_DIR.
    # RUNS_DIR = <mount_path>/runs (runs/ subdirectory, not share root).
    cloud_runs_dir = ""
    if backend != "local":
        _fs_server = MetaprocEnv.METAPROC_GCP_FILESTORE_SERVER.read_str(default="")
        if _fs_server:
            _fs_mount = MetaprocEnv.METAPROC_GCP_FILESTORE_MOUNT_PATH.read_str(
                default="/mnt/filestore"
            )
            cloud_runs_dir = _fs_mount + "/runs"

    if dry_run:
        assert adapter_obj is not None
        for item_context in item_contexts:
            item_vars = dict(variables)
            item_vars.update(item_context)
            if cloud_runs_dir:
                item_vars["RUNS_DIR"] = cloud_runs_dir
            item_runtime_config = cast(
                "dict[str, object]", resolve_runtime_config(merged_config, item_vars)
            )
            resolved_prompt, _, _ = prepare_step(
                spec,
                step_def,
                dict(item_vars),
                process_dir,
                effective_outputs or None,
                prompt_paths=target.prompt_paths,
                create_dirs=not bool(cloud_runs_dir),
            )
            enforce_no_unresolved_placeholders(
                resolved_prompt,
                context=f"step '{step}' prompt for {each}={item_context[each]}",
                allowed=allowed_runtime,
            )
            adapter_obj.build_command(Path("<prompt-file>"), item_runtime_config, item_vars)
        out.data(f"=== DRY RUN ===\nAdapter: {adapter_type}\nVariant: {effective_variant}")
        for bi, batch in enumerate(batches, 1):
            out.data(f"\nBatch {bi}/{n_batches}: {', '.join(c[each] for c in batch)}")
        return

    def _refresh_gcloud_token() -> None:
        """Resolve a GCP token into merged_config if needed.

        Uses ``google.auth`` auto-refresh via ``resolve_gcp_token()``.
        Clears stale api_key on failure so subsequent requests don't use
        an expired token.  Re-raises the original error.
        """
        if not _needs_gcloud_token:
            return

        try:
            merged_config["api_key"] = resolve_gcp_token()
        except Exception:
            merged_config.pop("api_key", None)
            raise

    # ── Pool-based execution ───────────────────────────────────────
    overall_start = time.monotonic()

    try:
        resolved_backend = get_backend(backend)
    except KeyError as exc:
        raise CLIError(str(exc)) from exc

    run_dir = compute_run_dir(spec, variables)

    # Scope pool state per worker when running on cloud VMs.
    # Without this, multiple workers clobber shared runpool-status.yaml on NFS.
    worker_id = MetaprocEnv.METAPROC_WORKER_ID.read_str(default=None)
    if worker_id:
        state_dir = paths_mod.worker_state_dir(run_dir, worker_id)
        logs_dir = paths_mod.worker_logs_dir(run_dir, worker_id)
    else:
        state_dir = paths_mod.run_state_dir(run_dir)
        logs_dir = paths_mod.runpool_logs_dir(run_dir)

    resource_config = target.resources or runtime_config
    pool_max_concurrency = max_concurrency or effective_batch_size
    max_concurrency_hint = resource_config.get("max_concurrency_hint")
    max_concurrency_hint_int: int | None = None
    if max_concurrency_hint is not None:
        max_concurrency_hint_int = int(str(max_concurrency_hint))
        pool_max_concurrency = min(pool_max_concurrency, max_concurrency_hint_int)

    profile_min_conc = resolve_min_concurrency(resource_config, default=POOL_MIN_CONCURRENCY)
    effective_min_concurrency = min_concurrency if min_concurrency is not None else profile_min_conc
    _pool_config = RunPoolConfig(
        max_concurrency=pool_max_concurrency,
        initial_concurrency=initial_concurrency or 0,
        min_concurrency=effective_min_concurrency,
        estimated_process_rss_bytes=resolve_estimated_process_rss_bytes(resource_config),
        initial_memory_budget_fraction=resolve_initial_memory_budget_fraction(resource_config),
        state_dir=state_dir,
        logs_dir=logs_dir,
        host_admission_enabled=resolved_backend.name == "local",
        host_admission_limit=resolve_host_max_concurrency(
            resource_config,
            default=pool_max_concurrency,
        ),
        execution_profile=effective_execution_profile,
        cli_max_concurrency=max_concurrency,
        batch_size=effective_batch_size,
        profile_max_concurrency_hint=max_concurrency_hint_int,
    )
    out.progress(
        f"run-parallel [pool]: max_concurrency={_pool_config.max_concurrency}, "
        f"min_concurrency={_pool_config.min_concurrency}"
    )

    # ── Auth-pool dispatch template (Phase 6) ────────
    # Construct the same PoolDispatchConfig run-process builds at
    # docs/arch/arch-metaproc-core.md
    # Phase 6. Without this, run-parallel as a worker entrypoint
    # ignores --auth-* flags and falls back to the legacy
    # single-credential bootstrap, which is exactly the cloud
    # multi-account gap. Workers do NOT re-run the
    # pre-fan-out probe gate — the orchestrator's run-process did
    # that once and persisted state transitions to the SM-backed
    # pool, so each worker's select_credential will already see any
    # expired/cooling marks.
    pool_dispatch_template: Any | None = _build_pool_dispatch_template(
        auth_account=auth_account,
        auth_backend=auth_backend,
        auth_fallback_policy=auth_fallback_policy,
        auth_include_labels=auth_include_labels,
        auth_exclude_labels=auth_exclude_labels,
        auth_cross_quota_group=auth_cross_quota_group,
        backend_name=backend,
        run_context=variables.get("RUN_ID", ""),
        out=out,
        auth_policy=auth_policy,
    )

    all_results_pool = asyncio.run(
        _run_agent_pool(
            spec=spec,
            step_def=step_def,
            step=step,
            each=each,
            variables=variables,
            item_contexts=item_contexts,
            adapter_type=adapter_type,
            merged_config=merged_config,
            effective_outputs=effective_outputs,
            effective_variant=effective_variant,
            allowed_runtime=allowed_runtime,
            retry_policy=retry_policy,
            process_dir=process_dir,
            step_hash=step_hash,
            prompt_paths=target.prompt_paths,
            target_env=target.env,
            refresh_token_fn=_refresh_gcloud_token,
            pool_config=_pool_config,
            backend=resolved_backend,
            out=out,
            cloud_runs_dir=cloud_runs_dir,
            pool_dispatch=pool_dispatch_template,
            preflight_quota_guard=auth_preflight_quota_guard,
        )
    )

    overall_elapsed = fmt_timedelta(time.monotonic() - overall_start)
    succeeded = sum(1 for _, rc in all_results_pool if rc == 0)
    failed_count = sum(1 for _, rc in all_results_pool if rc != 0)
    out.progress(f"\n=== Summary ({overall_elapsed}) ===")
    out.progress(f"  Total: {total} | Succeeded: {succeeded} | Failed: {failed_count}")

    if failed_count:
        failed_items = [item for item, rc in all_results_pool if rc != 0]
        out.progress(f"  Failed items: {', '.join(failed_items)}")
        raise typer.Exit(code=1)


def _build_prepare_launch(  # noqa: PLR0913
    *,
    shared: dict[str, Any],
    item_vars: dict[str, str],
    item_context: dict[str, str],
    attempt: int,
    spec: ProcessSpec,
    step_def: ProcessStep,
    step: str,
    each: str,
    process_dir: Path,
    merged_config: dict[str, object],
    effective_outputs: dict[str, IOSpec] | None,
    effective_variant: str,
    allowed_runtime: set[str],
    adapter_type: str,
    run_id: str,
    refresh_token_fn: object | None,
    step_hash: str | None = None,
    prompt_paths: list[str] | None = None,
    target_env: dict[str, str] | None = None,
    pool_status_path: Path | None = None,
    cloud_runs_dir: str = "",
    pool_dispatch: Any | None = None,
    pool: Any | None = None,
    lane_id: str | None = None,
    lane_execution_profile: str | None = None,
) -> Callable[[], PreparedLaunch]:
    """Build a prepare_launch closure that captures all context by value.

    Called at submit time; the returned closure runs at spawn time
    (after the pool's semaphore is acquired).
    """

    def _prepare() -> PreparedLaunch:
        # Refresh token at spawn time (not submit time).
        if refresh_token_fn is not None and callable(refresh_token_fn):
            refresh_token_fn()

        # For cloud backends with Filestore, override RUNS_DIR so template
        # resolution in the prompt and output paths uses the NFS mount path
        # instead of the orchestrator's local RUNS_DIR.
        resolve_vars = dict(item_vars)
        if cloud_runs_dir:
            resolve_vars["RUNS_DIR"] = cloud_runs_dir

        item_runtime_config = cast(
            "dict[str, object]", resolve_runtime_config(merged_config, resolve_vars)
        )
        # Call prepare_step with local item_vars so log directories are created
        # on the orchestrator (not at the cloud mount path).
        resolved_prompt, _logs_dir, log_path = prepare_step(
            spec,
            step_def,
            dict(item_vars),
            process_dir,
            effective_outputs or None,
            prompt_paths=prompt_paths or [],
        )
        if cloud_runs_dir:
            # Re-resolve prompt with cloud RUNS_DIR so the agent sees
            # Filestore NFS paths, not the orchestrator's local paths.
            # Skip dir creation — cloud paths don't exist locally.
            resolved_prompt, _, _ = prepare_step(
                spec,
                step_def,
                resolve_vars,
                process_dir,
                effective_outputs or None,
                prompt_paths=prompt_paths or [],
                create_dirs=False,
            )
        enforce_no_unresolved_placeholders(
            resolved_prompt,
            context=f"step '{step}' prompt for {each}={shared['item']}",
            allowed=allowed_runtime,
        )
        shared["log_path"] = Path(log_path)

        # Clean stale output files before retry so the agent starts fresh.
        # This handles two cases: (1) same-run retries after transient failure,
        # and (2) items orphaned from a killed run that discovery re-queued.
        # For fresh items the directory either doesn't exist yet or has no
        # output files, so this is a safe no-op.
        #
        # BEFORE unlinking, snapshot the failed-attempt content under
        # `<item_dir>/.attempts/attempt-<N>/` so post-mortem can read what
        # the agent actually produced on the failed attempt. Without this,
        # an invalid_output cascading to a permanent failure leaves only
        # the final attempt's artifact on disk and the actual failed content
        # is permanently lost.
        if shared["item_dir"] is not None and effective_outputs:
            resolved_outputs = resolve_record_output_paths(
                effective_outputs,
                resolve_vars,
            )
            attempt_number = int(shared.get("attempt_number", 1))
            attempts_root = Path(shared["item_dir"]) / ".attempts" / f"attempt-{attempt_number}"
            for output_path in resolved_outputs.values():
                stale = Path(output_path)
                if not stale.exists():
                    continue
                # Snapshot for post-mortem. Best-effort — never block the
                # retry on archive failure.
                try:
                    attempts_root.mkdir(parents=True, exist_ok=True)
                    archived = attempts_root / stale.name
                    if stale.is_dir():
                        if archived.exists():
                            shutil.rmtree(archived)
                        shutil.copytree(stale, archived)
                    else:
                        shutil.copy2(stale, archived)
                except OSError as snap_err:
                    log.warning(
                        "Could not snapshot failed attempt %s to %s: %s",
                        stale,
                        attempts_root,
                        snap_err,
                    )
                log.info("Cleaning stale output before retry: %s", stale)
                if stale.is_dir():
                    shutil.rmtree(stale)
                else:
                    stale.unlink()

        # Mark running at spawn time (after semaphore acquire).
        state_dir = shared["state_dir"]
        state_dir.mkdir(parents=True, exist_ok=True)
        shared["running_record"] = mark_running_at(
            state_dir,
            run_id=run_id,
            step_id=step,
            item=dict(item_context),
            attempt=attempt,
        )
        write_attempt_at(
            state_dir,
            AttemptRecord(
                run_id=run_id,
                step_id=step,
                item=dict(item_context),
                params=dict(resolve_vars),
                outputs=resolve_record_output_paths(effective_outputs or {}, resolve_vars),
                runtime={"adapter_type": adapter_type, "variant": effective_variant},
                step_hash=step_hash,
            ),
        )

        adapter = get_adapter(adapter_type)
        prompt_file = Path(log_path).with_suffix(".prompt.md")
        with atomic_output_file(prompt_file) as tmp_path:
            Path(tmp_path).write_text(resolved_prompt)
        cmd = adapter.build_command(
            prompt_file,
            item_runtime_config,
            resolve_vars,
        )
        env: dict[str, str] = adapter.prepare_env(dict(os.environ), item_runtime_config)
        env.update(_resolve_env(target_env, resolve_vars))
        if pool_status_path is not None:
            env["METAPROC_POOL_STATUS"] = str(pool_status_path)
        # Lane context lets the trace extractor and any downstream
        # tool-wrapper attach lane_id / execution_profile to spans.
        # Trace TOOL_USAGE_ATTRS already reserves these keys; the
        # value below is the lane the orchestrator picked for this
        # task — degenerate single-profile runs see the synthesized
        # lane id (= profile name).
        resolved_lane_id = shared.get("lane_id") or lane_id
        if resolved_lane_id:
            env["METAPROC_LANE_ID"] = str(resolved_lane_id)
        if lane_execution_profile:
            env.setdefault("EXECUTION_PROFILE", str(lane_execution_profile))

        # Credential pool dispatch integration (plan-2026-04-21 P2.4/P2.6).
        # Opt-in via pool_dispatch; None means the legacy single-credential
        # path (adapter.bootstrap populates ~/.claude etc. on the worker).
        # When enabled, we lease a per-item slot here and stash the lease
        # on shared[] so _process_completion can tear it down exactly once.
        # shared["pool_exclude"] accumulates across retries so the
        # fallback walk skips the labels that already cooled off on
        # earlier attempts for this same item (P2.6: coordinator walks
        # forward through the compat list rather than looping back).
        #
        # Adapter-type gate: the pool is set per-dispatch (e.g.
        # ``--auth-account claude-code-cli``) and applies cleanly to
        # steps that USE that adapter (process-item, retrieve-precedent).
        # Steps that use a DIFFERENT adapter — e.g. mine-adhoc with
        # pi-cli + vertex-maas — must NOT lease a claude slot: the slot
        # path appends adapter-specific debug flags (``claude -d api``) to
        # the cmd, which pi-cli rejects with ``Unknown option: -d`` and
        # exits 1 in <10s; the runpool then classifies this as a crash
        # and cools the underlying Claude credential, eventually
        # exhausting the pool. Skipping the pool path for non-matching
        # adapter types keeps mine-adhoc on the legacy single-credential
        # path (or no auth at all for pi-cli/vertex-maas which uses GCP
        # access tokens).
        if pool_dispatch is not None and adapter_type == pool_dispatch.adapter:
            from metaproc.adapters.registry import (  # noqa: PLC0415 -- pre-existing local import; needs review
                get_auth_capable,
            )
            from metaproc.dispatch.pool_dispatch import (  # noqa: PLC0415 -- pre-existing local import; needs review
                acquire_slot,
                auth_override_refusal_keys,
                build_auth_lease_acquired,
                compose_slot_env,
            )

            item_exclude_list = shared.get("pool_exclude") or []
            lease = acquire_slot(
                pool_dispatch,
                item=shared["item"],
                attempt=attempt,
                item_exclude=tuple(item_exclude_list),
                session_log_path=Path(log_path),
            )
            shared["slot_lease"] = lease
            # Schema-v2 acquisition event — emitted before subprocess
            # spawn so post-hoc analysis can pair with the eventual
            # auth_outcome by primary key (see plan-2026-05-03 §
            # auth_lease_acquired event). Pool may be None on legacy
            # call paths (in-tree probes); bail quietly in that case.
            if pool is not None:
                # Snapshot the in-process active-lease counter for the
                # lease's adapter — JSON-safe label-keyed dict so the
                # event downstream doesn't carry tuple keys (upstream change
                # review P2). The counter has the lease's own +1
                # already applied via slot_coordinator.acquire_slot.
                counts = pool_dispatch.coordinator.active_counter.snapshot()
                active_for_adapter = {
                    label_key: count
                    for (adapter_key, label_key), count in counts.items()
                    if adapter_key == lease.adapter
                }
                payload = build_auth_lease_acquired(
                    lease,
                    policy=str(pool_dispatch.strategy.policy),
                    active_lease_count=active_for_adapter,
                )
                pool.record_auth_lease_acquired(payload)
            env = compose_slot_env(
                env,
                lease=lease,
                refuse_on_keys=auth_override_refusal_keys(lease.adapter),
            )
            # Append per-adapter API-debug-capture flags so OAuth refresh
            # status and per-attempt API errors land in <slot>/claude-code-debug.log
            # for the classifier to read post-run.
            auth_adapter = get_auth_capable(lease.adapter)
            if auth_adapter is not None:
                cmd = [*cmd, *auth_adapter.debug_capture_args(lease.slot_dir)]
        # Cloud backends use metadata to set container env vars.
        # Adapter-identity keys (adapter_type/provider/trace_source/model) make
        # the sidecar self-describing so trace extractors don't need to infer
        # the adapter from argv[0]. lane_id + execution_profile let multi-lane
        # batch consumers (e.g. metaproc compare-matrix) join spans cleanly.
        launch_metadata: dict[str, str] = {
            "run_id": run_id,
            "item_id": shared["item"],
            "variant": effective_variant,
            "process_dir": str(process_dir),
            "step": step,
            "vars_json": json.dumps(item_vars),
            "adapter_type": adapter_type,
            "adapter_provider": adapter_provider(adapter_type),
            "adapter_trace_source": adapter_trace_source(adapter_type),
            # Resolved from the env this adapter is actually spawned with, so a
            # stray API key that flips a subscription run onto pay-per-token is
            # recorded at the moment it happens rather than inferred later.
            "billing_class": resolve_billing_class(adapter_type, env),
        }
        adapter_model = item_runtime_config.get("model")
        if isinstance(adapter_model, str) and adapter_model:
            launch_metadata["adapter_model"] = adapter_model
        if lane_id:
            launch_metadata["lane_id"] = lane_id
        if lane_execution_profile:
            launch_metadata["execution_profile"] = lane_execution_profile
        # Pass repo URL if configured (for git-based cloud I/O).
        repo_url = MetaprocEnv.METAPROC_REPO_URL.read_str(default="")
        if repo_url:
            launch_metadata["repo_url"] = repo_url
        run_branch = MetaprocEnv.METAPROC_RUN_BRANCH.read_str(default="")
        if run_branch:
            launch_metadata["run_branch"] = run_branch

        return PreparedLaunch(
            command=tuple(cmd),
            env=env,
            cwd=adapter.working_directory(item_runtime_config),
            log_path=Path(log_path),
            filter_log=adapter_type == "pi-cli",
            metadata=launch_metadata,
        )

    return _prepare


async def _run_agent_pool(  # noqa: PLR0913
    *,
    spec: ProcessSpec,
    step_def: ProcessStep,
    step: str,
    each: str,
    variables: dict[str, str],
    item_contexts: list[dict[str, str]],
    adapter_type: str,
    merged_config: dict[str, object],
    effective_outputs: dict[str, IOSpec] | None,
    effective_variant: str,
    allowed_runtime: set[str],
    retry_policy: RetryPolicy,
    process_dir: Path,
    refresh_token_fn: object | None,
    pool_config: RunPoolConfig,
    backend: LaunchBackend | None,
    out: Any,
    step_hash: str | None = None,
    prompt_paths: list[str] | None = None,
    target_env: dict[str, str] | None = None,
    cloud_runs_dir: str = "",
    pool_dispatch: Any | None = None,
    preflight_quota_guard: str = "warn",
) -> list[tuple[str, int]]:
    """Run agent-mode items through RunPool with adaptive concurrency.

    The pool manages process lifecycle, concurrency, memory-pressure
    adaptation, and per-process health monitoring.  This function handles
    state recording (mark_running/completed/failed), output validation,
    and retry orchestration.

    When *pool_dispatch* is supplied (plan-2026-04-21-auth-credential-
    pool.md P2.4), each item leases a per-attempt credential slot before
    spawn and writes an auth_outcome event at completion. When ``None``
    the legacy single-credential path via ``adapter.bootstrap(home)``
    runs unchanged.
    """
    run_context = variables.get("RUN_ID", variables.get("DATE", variables.get("SCOPE", "")))
    run_id = f"{spec.name}/{run_context}"
    run_dir = compute_run_dir(spec, variables)

    stall_timeout_s: float | None = (
        POOL_STALL_TIMEOUT_MINUTES * 60 if POOL_STALL_TIMEOUT_MINUTES is not None else None
    )

    pool = RunPool(pool_config, backend=backend)
    all_results: list[tuple[str, int]] = []
    worker_id = MetaprocEnv.METAPROC_WORKER_ID.read_str(default="")

    # Per-step preflight quota gate.
    # Only fires when the step uses the auth pool — non-pool dispatches
    # have no headroom signal to gate on. Posture comes from the
    # operator's --auth-preflight-quota-guard flag.
    if (
        pool_dispatch is not None
        and adapter_type == pool_dispatch.adapter
        and preflight_quota_guard != "off"
    ):
        # Local import: dispatch.preflight isn't needed on the no-pool
        # fast path (most run-parallel invocations); keep it lazy here.
        from metaproc.dispatch.preflight import (  # noqa: PLC0415 -- pre-existing local import; needs review
            GuardPosture,
            check_step_preflight,
        )

        verdict = check_step_preflight(
            pool_dispatch.coordinator.backend,
            adapter=pool_dispatch.adapter,
            step_id=step,
            fan_out_size=len(item_contexts),
            posture=cast(GuardPosture, preflight_quota_guard),
        )
        if verdict.status == "refuse":
            raise CLIError(
                f"Pre-fan-out quota gate refused dispatch for step {step!r}:\n{verdict.message}"
            )
        if verdict.status == "warn":
            log.warning(
                "preflight quota gate: step=%s status=warn projected=%s headroom=%s",
                step,
                verdict.projected_units,
                verdict.headroom.pool_remaining_units,
            )
            out.warning(
                f"Pre-fan-out quota gate: status=warn for step {step!r} "
                f"({len(item_contexts)} items projected). Continuing per posture=warn."
            )

    # Orchestrator-owned scheduler: three structures replace the old
    # submit-all / await-all / retry-all batch loop.
    not_started: deque[dict[str, str]] = deque(item_contexts)
    active: dict[asyncio.Future[Any], dict[str, Any]] = {}
    # Heap entries: (ready_at, tiebreaker, shared, item_context)
    retry_heap: list[tuple[float, int, dict[str, Any], dict[str, str]]] = []
    retry_seq = 0  # Monotonic tiebreaker to avoid comparing dicts.

    def _submit_item(
        item_context: dict[str, str],
        shared: dict[str, Any],
    ) -> None:
        """Build ProcessConfig and submit to pool, handling failures inline."""
        item = shared["item"]
        item_dir = shared["item_dir"]
        state_dir = shared["state_dir"]

        # Defensive: if the item's declared outputs already validate on disk,
        # promote state to completed and skip the spawn. Guards against the
        # exit-code classifier marking outputs-valid items as `state: failed`
        # (e.g. cosmetic warnings on agent exit causing claude-startup-exit-1-silent
        # misclass), which would otherwise drive cleanup-stale-output to destroy
        # valid work + re-spawn at full token cost. The post-cleanup
        # validate-then-mark loop at line 921 already runs on the happy path;
        # this duplicates it pre-cleanup so a misclassified failure can recover
        # without re-running the agent.
        if item_dir is not None and effective_outputs:
            item_vars_for_check = dict(shared.get("item_vars", {}))
            try:
                pre_existing_errors = validate_item_outputs(
                    item_dir, effective_outputs, variables=item_vars_for_check
                )
            except Exception as _exc:
                pre_existing_errors = [f"validation raised: {_exc}"]
            if not pre_existing_errors:
                log.info(
                    "Pre-spawn outputs validate for %s=%s; promoting to completed "
                    "and skipping agent invocation (recovery from misclassified failure)",
                    each,
                    item,
                )
                mark_completed_at(state_dir)
                all_results.append((item, 0))
                out.progress(
                    f"  Skipped {each}={item} (outputs already valid; promoted to completed)"
                )
                return

        prepare_fn = _build_prepare_launch(
            shared=shared,
            item_vars=dict(shared["item_vars"]),
            item_context=dict(item_context),
            attempt=shared["attempt_number"],
            spec=spec,
            step_def=step_def,
            step=step,
            each=each,
            process_dir=process_dir,
            merged_config=merged_config,
            effective_outputs=effective_outputs,
            effective_variant=effective_variant,
            allowed_runtime=allowed_runtime,
            adapter_type=adapter_type,
            prompt_paths=prompt_paths,
            target_env=target_env,
            run_id=run_id,
            refresh_token_fn=refresh_token_fn,
            step_hash=step_hash,
            pool_status_path=pool._status_path,  # noqa: SLF001
            cloud_runs_dir=cloud_runs_dir,
            pool_dispatch=pool_dispatch,
            pool=pool,
            lane_id=pool_config.execution_profile,
            lane_execution_profile=pool_config.execution_profile,
        )

        # Resolve config at submit time for timeout extraction.
        # _build_prepare_launch re-resolves at spawn time to pick up
        # token refreshes — the two calls are intentionally separate.
        item_runtime_config = cast(
            "dict[str, object]", resolve_runtime_config(merged_config, shared["item_vars"])
        )
        pool_timeout_s = (
            int(str(item_runtime_config["timeout_s"]))
            if item_runtime_config.get("timeout_s") is not None
            else None
        )
        config = ProcessConfig(
            prepare_launch=prepare_fn,
            label=f"{each}={item}",
            timeout_s=pool_timeout_s,
            stall_timeout_s=stall_timeout_s,
            metadata={"shared": shared},
            lane_id=pool_config.execution_profile,
            execution_profile=pool_config.execution_profile,
        )
        try:
            future = pool.submit(config)
        except Exception as exc:
            error_str = f"submission failed: {exc}"
            out.progress(f"  {each}={item}: {error_str}")
            state_dir.mkdir(parents=True, exist_ok=True)
            mark_failed_synthetic_at(
                state_dir,
                run_id=run_id,
                step_id=step,
                item=dict(item_context),
                attempt=shared["attempt_number"],
                error=error_str,
            )
            _classify_and_maybe_retry(shared, error_str)
            return
        active[future] = shared
        out.progress(f"  Queued {each}={item} (attempt {shared['attempt_number']})")

    def _pool_teardown(
        shared: dict[str, Any], *, error_str: str | None
    ) -> AuthFailureClassification | None:
        """Closure wrapper around :func:`_teardown_pool_slot`.

        Threads the captured ``pool_dispatch`` / ``pool`` handles into
        the module-level helper that holds all the teardown semantics;
        keeps call sites inside ``_run_agent_pool`` terse.
        """
        return _teardown_pool_slot(
            pool_dispatch=pool_dispatch,
            pool=pool,
            shared=shared,
            error_str=error_str,
        )

    def _classify_and_maybe_retry(shared: dict[str, Any], error_str: str) -> None:
        """Classify failure and either schedule retry or record permanent failure.

        Plan §Phase 5: the pool adapter's severity verdict takes
        precedence over the generic retry classifier. When the auth
        classification reports ``effective_severity() == ABORT`` (a
        dead refresh token, an expired/unauthorized credential, or a
        matched known-bug signature) we skip the retry heap regardless
        of what ``classify_error`` says — retrying past ABORT wastes
        attempts and, for known bugs, re-fires the exact failure.

        When the failure is "no eligible pool label"
        (PoolSlotUnavailableError surfaced from acquire_slot during
        prepare_launch), schedule a cooling-aware retry that pauses
        until the earliest pool slot's ``cooling_until_ts`` rather
        than burning compute_backoff and exhausting max_retries
        against a known-stuck pool. This keeps the orchestrator alive
        instead of cascade-failing through a 5-hour Pro-bucket window.
        """
        nonlocal retry_seq
        item = shared["item"]
        fc = classify_failure(error_str)
        # When the error is a Claude quota exhaustion ("out of extra usage ·
        # resets 3:10am"), parse the reset clock so the pool can pause the
        # whole cohort until the named window passes — replaces the 2026-05-13
        # per-task crash cascade. parse_quota_reset_at returns None for any
        # other error string; pool.record_failure_class then no-ops the pause.
        quota_reset_at = (
            parse_quota_reset_at(error_str) if fc == FailureClass.QUOTA_EXHAUSTED else None
        )
        pool.record_failure_class(fc, quota_reset_at=quota_reset_at)
        verdict = classify_error(error_str)
        # Tear down pool slot (if any) BEFORE we schedule the retry —
        # the retry's prepare_launch will lease a fresh slot against
        # the failed attempt's excluded label set (P2.6).
        auth_classification = _pool_teardown(shared, error_str=error_str)
        force_abort = _auth_forces_abort(auth_classification)
        # When the item itself hit quota exhaustion with a parsed reset
        # clock, schedule a retry-after-reset rather than burning a normal
        # backoff attempt or permanent-failing. Mirrors the pool-cooling
        # retry path below: don't increment attempt_number because the
        # item never had a real shot at landing — the upstream cap blocked
        # it. Without this branch, classify_error returns FAIL for quota
        # patterns and the very item that discovered the cap gets lost
        # while the pool quietly pauses for everything else (upstream change review
        # finding #1).
        is_quota_exhausted_retry = quota_reset_at is not None and not force_abort
        if is_quota_exhausted_retry:
            assert quota_reset_at is not None  # noqa: S101 — narrow the type for mypy/basedpyright
            now_local = datetime.now(quota_reset_at.tzinfo)
            quota_delay = max(
                0.0, (quota_reset_at - now_local).total_seconds() + _QUOTA_RESET_BUFFER_S
            )
            retry_seq += 1
            heapq.heappush(
                retry_heap,
                (time.monotonic() + quota_delay, retry_seq, shared, shared["item_context"]),
            )
            pool.record_retry_scheduled(f"{each}={item}", shared["attempt_number"], quota_delay)
            out.progress(
                f"  {each}={item}: quota exhausted -- waiting {quota_delay:.0f}s "
                f"for reset_at={quota_reset_at.isoformat()} (attempt {shared['attempt_number']})"
            )
            return
        # Detect pool-exhausted failures and override the
        # retry strategy. The error_str shape is "launch failed: no
        # eligible pool label for adapter=..." (see PoolSlotUnavailableError).
        is_pool_exhausted = pool_dispatch is not None and "no eligible pool label" in error_str
        if is_pool_exhausted and not force_abort:
            cooling_delay = _compute_pool_cooling_delay(pool_dispatch)
            retry_seq += 1
            heapq.heappush(
                retry_heap,
                (time.monotonic() + cooling_delay, retry_seq, shared, shared["item_context"]),
            )
            # Don't increment attempt_number for pool-wait retries —
            # the item never actually launched; we're just waiting on
            # quota recovery. Operator's max_retries should bound real
            # attempts, not bucket-wait cycles.
            pool.record_retry_scheduled(f"{each}={item}", shared["attempt_number"], cooling_delay)
            out.progress(
                f"  {each}={item}: pool exhausted -- waiting {cooling_delay:.0f}s "
                f"for slot recovery (attempt {shared['attempt_number']})"
            )
            return
        if (
            not force_abort
            and verdict == RetryVerdict.RETRY
            and shared["attempt_number"] <= max_retries_for(fc, retry_policy.max_retries)
        ):
            shared["attempt_number"] += 1
            retry_index = shared["attempt_number"] - 1
            delay = compute_backoff(retry_index, retry_policy)
            retry_seq += 1
            heapq.heappush(
                retry_heap,
                (time.monotonic() + delay, retry_seq, shared, shared["item_context"]),
            )
            pool.record_retry_scheduled(f"{each}={item}", shared["attempt_number"], delay)
            out.progress(
                f"  {each}={item}: retryable [{fc}] -- retry scheduled "
                f"(attempt {shared['attempt_number']}, backoff {delay:.0f}s)"
            )
        else:
            all_results.append((item, 1))
            if force_abort and auth_classification is not None:
                known_bug = auth_classification.known_bug_signature
                if known_bug:
                    abort_tag = f"known-bug:{known_bug}"
                else:
                    abort_tag = f"auth-abort:{auth_classification.status}"
                out.progress(f"  {each}={item}: permanent failure [{abort_tag}] ({error_str})")
            else:
                out.progress(f"  {each}={item}: permanent failure [{fc}] ({error_str})")

    def _process_completion(future: asyncio.Future[Any], shared: dict[str, Any]) -> None:
        """Handle a completed future — success, failure, or exception."""
        item = shared["item"]
        item_dir = shared["item_dir"]
        state_dir = shared["state_dir"]
        log_path = shared["log_path"]
        running_record = shared["running_record"]
        item_context = shared["item_context"]

        # Handle future exceptions (launch failures from prepare_launch or backend).
        try:
            result = future.result()
        except Exception as exc:
            error_str = f"launch failed: {exc}"
            out.progress(f"  {each}={item}: {error_str}")
            state_dir.mkdir(parents=True, exist_ok=True)
            if running_record is None:
                # prepare_launch raised before mark_running — write synthetic status.
                mark_failed_synthetic_at(
                    state_dir,
                    run_id=run_id,
                    step_id=step,
                    item=dict(item_context),
                    attempt=shared["attempt_number"],
                    error=error_str,
                )
            else:
                mark_failed_at(state_dir, error=error_str, running_record=running_record)
            _classify_and_maybe_retry(shared, error_str)
            return

        if result.kill_reason is not None:
            error_str = result.kill_reason
            if result.kill_reason == "timeout":
                error_str = f"timeout after {result.elapsed_s:.0f}s"
            elif result.kill_reason == "stalled":
                error_str = f"stalled (no log output for {stall_timeout_s:.0f}s)"
            out.progress(f"  Killed {each}={item}: {error_str} ({fmt_timedelta(result.elapsed_s)})")
            mark_failed_at(state_dir, error=error_str, running_record=running_record)
            # Classify BEFORE compacting. ``try_compact_log`` rewrites the
            # log via ``atomic_output_file``, which has a documented brief
            # window where the original path is absent (between rename to
            # ``.bak`` and rename of the temp file into place). The 2026-04-27
            # multi-label mis-classification was the failure mode: classifier
            # ran during that window, saw no session log, and fell through
            # to the bug-regex on stderr "exit code 1".
            _classify_and_maybe_retry(shared, error_str)
            if log_path is not None:
                try_compact_log(log_path)
        elif result.exit_code == 0:
            # _handle_success may call _classify_and_maybe_retry via batch_failed
            # for output validation failures. We pass a local list for it.
            success_failed: list[tuple[dict[str, Any], str]] = []
            _handle_success(
                each,
                item,
                item_dir,
                state_dir,
                item_context,
                log_path,
                running_record,
                effective_outputs,
                variables,
                run_id,
                step,
                result,
                out,
                all_results,
                success_failed,
                shared,
                step_hash=step_hash,
                cloud_runs_dir=cloud_runs_dir,
            )
            # Handle output validation failures from _handle_success.
            if success_failed:
                for failed_shared, failed_error in success_failed:
                    _classify_and_maybe_retry(failed_shared, failed_error)
            else:
                # Pure-success path: tear down the pool slot with
                # ok semantics so adapter.flush_refreshed_credential
                # can write back a rotated blob if the CLI refreshed.
                _pool_teardown(shared, error_str=None)
        else:
            error_str = f"exit code {result.exit_code}"
            if log_path is not None:
                log_error = extract_log_error(log_path)
                if log_error:
                    error_str = f"{error_str} (log: {log_error})"
            mark_failed_at(state_dir, error=error_str, running_record=running_record)
            out.progress(
                f"  Done step={step} {each}={item} "
                f"attempt={shared.get('attempt_number', 1)} "
                f"status=exit_{result.exit_code} "
                f"({fmt_timedelta(result.elapsed_s)})"
            )
            # Classify BEFORE compacting (see kill-path above for why).
            _classify_and_maybe_retry(shared, error_str)
            if log_path is not None:
                try_compact_log(log_path)

    def _fill_pool() -> None:
        """Fill available pool slots: retries-due first, then first-pass items."""
        snapshot = pool.snapshot
        # pool.active_count = running processes, pool.pending_count = submitted
        # but waiting on the semaphore.  We want to fill up to current_concurrency
        # without over-queuing on the semaphore (the orchestrator IS the queue).
        capacity = max(
            0, snapshot.current_concurrency - snapshot.active_count - snapshot.pending_count
        )

        while capacity > 0 and (retry_heap or not_started):
            # Priority 1: retries whose backoff has elapsed.
            now = time.monotonic()
            if retry_heap and retry_heap[0][0] <= now:
                _, _, shared, item_context = heapq.heappop(retry_heap)
                pool.record_retry_consumed(f"{each}={shared['item']}")
                if worker_id and not claim_item(
                    run_dir,
                    step,
                    worker_id=worker_id,
                    item=shared["item"],
                ):
                    out.progress(f"  {each}={shared['item']}: claimed by another worker — skipping")
                    all_results.append((shared["item"], 0))
                    continue
                _submit_item(item_context, shared)
                capacity -= 1
            elif not_started:
                # Priority 2: untouched first-pass items.
                item_context = not_started.popleft()
                item = item_context[each]
                item_vars = dict(variables)
                item_vars.update(item_context)
                item_dir = compute_item_dir(effective_outputs or {}, item_vars)
                state_dir = compute_task_state_dir(run_dir, step_def, item_vars)
                shared: dict[str, Any] = {
                    "item": item,
                    "item_context": dict(item_context),
                    "item_vars": dict(item_vars),
                    "item_dir": item_dir,
                    "state_dir": state_dir,
                    "running_record": None,
                    "log_path": None,
                    "attempt_number": 1,
                    "lane_id": pool_config.execution_profile,
                }
                current_status = read_status_at(state_dir)
                if (
                    current_status is not None
                    and current_status.state == "completed"
                    and current_status.step_id == step
                ):
                    out.progress(f"  {each}={item}: already completed — skipping")
                    all_results.append((item, 0))
                    continue
                if worker_id and not claim_item(
                    run_dir,
                    step,
                    worker_id=worker_id,
                    item=item,
                ):
                    out.progress(f"  {each}={item}: claimed by another worker — skipping")
                    all_results.append((item, 0))
                    continue
                _submit_item(item_context, shared)
                capacity -= 1
            else:
                break

    try:
        while not_started or active or retry_heap:
            _fill_pool()

            if active:
                # Wake periodically even without retry work. The RunPool pressure
                # monitor can raise current_concurrency while all active items are
                # still running; without a timeout, this scheduler waits for the
                # first completion and leaves new capacity idle.
                timeout = _POOL_FILL_POLL_INTERVAL_S
                if retry_heap:
                    timeout = min(timeout, max(0.1, retry_heap[0][0] - time.monotonic()))

                done, _ = await asyncio.wait(
                    active.keys(),
                    return_when=asyncio.FIRST_COMPLETED,
                    timeout=timeout,
                )
                for future in done:
                    shared = active.pop(future)
                    _process_completion(future, shared)
            elif retry_heap:
                # No active futures, but retries are waiting for backoff.
                sleep_time = max(0, retry_heap[0][0] - time.monotonic())
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
    finally:
        await pool.shutdown()

    return all_results


def _handle_success(  # noqa: PLR0913
    each: str,
    item: str,
    item_dir: Path | None,
    state_dir: Path,
    item_context: dict[str, str],
    log_path: Path | None,
    running_record: Any,
    effective_outputs: dict[str, IOSpec] | None,
    variables: dict[str, str],
    run_id: str,
    step: str,
    result: Any,
    out: Any,
    all_results: list[tuple[str, int]],
    batch_failed: list[tuple[dict[str, Any], str]],
    shared: dict[str, Any],
    step_hash: str | None = None,
    cloud_runs_dir: str = "",
) -> None:
    """Handle a successful process exit — validate outputs, record state."""
    if item_dir is not None and effective_outputs:
        if cloud_runs_dir:
            check_dir = Path(cloud_runs_dir) / item_dir.parent.name / item_dir.name
        else:
            check_dir = item_dir

        item_vars = {**variables, **item_context}
        for io_spec in effective_outputs.values():
            if hasattr(io_spec, "path") and io_spec.path:
                out_file = check_dir / Path(io_spec.path).name
                if out_file.exists() and repair_frontmatter_file(out_file):
                    out.progress(f"  Repaired YAML in {out_file.name} for {item}")
        output_errors = validate_item_outputs(check_dir, effective_outputs, variables=item_vars)
        validated = not bool(output_errors)
        if output_errors:
            error_str = f"output validation failed: {'; '.join(output_errors)}"
            if log_path is not None:
                log_error = extract_log_error(log_path)
                if log_error:
                    error_str = f"{error_str} (log: {log_error})"
            mark_failed_at(state_dir, error=error_str, running_record=running_record)
            out.progress(
                f"  Done step={step} {each}={item} "
                f"attempt={shared.get('attempt_number', 1)} "
                f"status=invalid_outputs "
                f"({fmt_timedelta(result.elapsed_s)})"
            )
            batch_failed.append((shared, error_str))
            if log_path is not None:
                try_compact_log(log_path)
            return

        completion_vars = dict(variables)
        completion_vars.update(item_context)
        state_dir.mkdir(parents=True, exist_ok=True)
        mark_completed_at(state_dir, running_record=running_record)
        write_result_at(
            state_dir,
            ResultRecord(
                run_id=run_id,
                step_id=step,
                state="completed",
                validated=validated,
                outputs=resolve_record_output_paths(effective_outputs, completion_vars),
                published_at=datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S"),
                step_hash=step_hash,
            ),
        )

    out.progress(
        f"  Done step={step} {each}={item} "
        f"attempt={shared.get('attempt_number', 1)} "
        f"status=ok "
        f"({fmt_timedelta(result.elapsed_s)})"
    )
    all_results.append((item, 0))
    if log_path is not None:
        try_compact_log(log_path)
