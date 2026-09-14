"""Outer host admission for the scalar agent execution path.

``run-process`` agent leaves acquire this gate before submitting to the run-owned
RunPool, whose internal host admission is disabled to avoid acquiring twice. The same
gate also covers scalar launches without a run-owned pool. Mapped agent leaves can
reach this path too; the scalar execution function does not imply a scalar-only scope.

The intended resource contract is design test 7 of
``src/metaproc/docs/process-framework-theory.md`` ("Is every launch admitted?"):

    Every independently scheduled task attempt is admitted through the applicable
    resource authorities, and its full process tree is charged to that attempt.

Admission is best-effort in one specific sense: if the gate cannot be reached or the
wait times out, the launch proceeds rather than failing the run. A step that would have
run before this module existed must not start failing because the shared slot directory
is unwritable; the point is to bound normal operation, not to add a new outage mode.
Each bypass is recorded as ``host_admission_denied`` with ``decision: bypass`` and the
seconds spent waiting, so ungoverned launches can be counted from the event log.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path

from metaproc.runpool.events import EventLogger
from metaproc.runpool.host_admission import (
    DEFAULT_HOST_ADMISSION_NAMESPACE,
    HostAdmissionGate,
    HostAdmissionLease,
)
from metaproc.runpool.pool import resolve_host_max_concurrency

logger = logging.getLogger(__name__)

# The scalar gate shares one namespace with every fan-out pool on the host, and a gate
# with limit N contends only for slots 0..N-1. Two consequences drive these constants.
# A low limit means scalar launches contend for a prefix of the slots that busy pools
# fill first, so the acquire timeout is the difference between a safety valve and a
# stall: at the gate's default 300s, a saturated host added five minutes of dead wait
# to every scalar step and then launched it ungoverned anyway. And a limit of 1 would
# serialize unrelated scalar steps even on an idle machine, which nothing in the
# incident record motivates — the recorded failures were dozens of simultaneous
# launches, not two. The default limit applies only to a leaf without a run-owned
# RunPool; resolve_agent_leaf_host_limit owns the full precedence.
SCALAR_DEFAULT_HOST_LIMIT = 4
SCALAR_ACQUIRE_TIMEOUT_S = 60.0


def resolve_agent_leaf_host_limit(
    resource_config: Mapping[str, object],
    *,
    pool_max_concurrency: int | None,
) -> int:
    """Resolve the host-slot limit for one ``run-process`` agent leaf.

    Precedence:
    1. ``METAPROC_HOST_MAX_LOCAL_AGENTS`` lowers the result and never raises it.
    2. An explicit ``host_max_concurrency`` in the leaf's resources.
    3. ``pool_max_concurrency`` when the leaf runs under a run-owned RunPool, so the
       gate has a slot for every agent that pool can run.
    4. ``SCALAR_DEFAULT_HOST_LIMIT`` for a leaf without a run-owned pool.

    A leaf holds its slot from before pool submission until its process exits, so a
    limit below the run's leaf ceiling makes leaves wait on slots that the same run's
    queued leaves hold.
    """
    default = SCALAR_DEFAULT_HOST_LIMIT if pool_max_concurrency is None else pool_max_concurrency
    return resolve_host_max_concurrency(resource_config, default=default)


@asynccontextmanager
async def admitted_launch(
    *,
    enabled: bool,
    limit: int,
    label: str,
    pool_id: str,
    root_dir: Path | None = None,
    namespace: str = DEFAULT_HOST_ADMISSION_NAMESPACE,
    metadata: Mapping[str, object] | None = None,
    event_logger: EventLogger | None = None,
) -> AsyncGenerator[HostAdmissionLease | None]:
    """Hold a host admission slot around one scalar-path launch.

    Yields the lease, or ``None`` when admission is disabled or unavailable. Release is
    attempted on exit regardless of launch outcome. Release I/O failure can leave the
    lease in place until later stale reclamation.
    """
    if not enabled or limit <= 0:
        yield None
        return

    gate = HostAdmissionGate(
        root_dir=root_dir,
        namespace=namespace,
        limit=limit,
        acquire_timeout_s=SCALAR_ACQUIRE_TIMEOUT_S,
    )
    lease: HostAdmissionLease | None = None

    def record_wait() -> None:
        if event_logger is not None:
            event_logger.host_admission_denied(
                namespace=gate.namespace,
                limit=limit,
                label=label,
                reason="no_available_slot",
                decision="wait",
            )

    started = time.monotonic()
    try:
        lease = await gate.acquire(
            label=label,
            pool_id=pool_id,
            metadata=dict(metadata or {}),
            on_wait=record_wait,
        )
    except OSError as exc:
        waited_s = time.monotonic() - started
        if event_logger is not None:
            event_logger.host_admission_denied(
                namespace=gate.namespace,
                limit=limit,
                label=label,
                reason="timeout" if isinstance(exc, TimeoutError) else "unavailable",
                decision="bypass",
                error=str(exc),
                waited_s=waited_s,
            )
        logger.warning(
            "host admission unavailable for %s after %.1fs at limit %d (%s); "
            "launching without a slot",
            label,
            waited_s,
            limit,
            exc,
        )

    try:
        yield lease
    finally:
        if lease is not None:
            try:
                gate.release(lease)
            except OSError as exc:
                logger.warning("could not release host admission slot for %s: %s", label, exc)
