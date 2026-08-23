"""Host admission for launches that do not go through a pool.

RunPool admits every process it launches, but it is constructed only on the fan-out
path. A step with no ``for_each`` launches its adapter subprocess directly, so on a host
running several processes at once those launches are invisible to each other and to the
pool: no slot accounting, no memory backpressure, nothing to stop N orchestrators from
starting N agents simultaneously.

This closes that gap for the scalar path without pulling in the rest of RunPool. The
enforceable rule is design test 7 of
``docs/process-framework-concepts.md`` ("Is every launch admitted?"):

    Every independently scheduled task attempt is admitted through the applicable
    resource authorities, and its full process tree is charged to that attempt.

Admission is best-effort in one specific sense: if the gate cannot be reached or the
wait times out, the launch proceeds rather than failing the run. A step that would have
run before this module existed must not start failing because the shared slot directory
is unwritable; the point is to bound normal operation, not to add a new outage mode.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path

from metaproc.runpool.host_admission import (
    DEFAULT_HOST_ADMISSION_NAMESPACE,
    HostAdmissionGate,
    HostAdmissionLease,
)

logger = logging.getLogger(__name__)

# The scalar gate shares one namespace with every fan-out pool on the host, and a gate
# with limit N contends only for slots 0..N-1. Two consequences drive these constants.
# A low limit means scalar launches contend for a prefix of the slots that busy pools
# fill first, so the acquire timeout is the difference between a safety valve and a
# stall: at the gate's default 300s, a saturated host added five minutes of dead wait
# to every scalar step and then launched it ungoverned anyway. And a limit of 1 would
# serialize unrelated scalar steps even on an idle machine, which nothing in the
# incident record motivates — the recorded failures were dozens of simultaneous
# launches, not two. METAPROC_HOST_MAX_LOCAL_AGENTS still overrides both ways when an
# operator sets it.
SCALAR_DEFAULT_HOST_LIMIT = 4
SCALAR_ACQUIRE_TIMEOUT_S = 60.0


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
) -> AsyncGenerator[HostAdmissionLease | None]:
    """Hold a host admission slot for the duration of one direct launch.

    Yields the lease, or ``None`` when admission is disabled or unavailable. The slot is
    released on the way out whether or not the launch succeeded, so a crashing adapter
    cannot leak capacity.
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
    try:
        lease = await gate.acquire(
            label=label,
            pool_id=pool_id,
            metadata=dict(metadata or {}),
        )
    except TimeoutError:
        logger.warning(
            "host admission timed out for %s after %ss; launching without a slot",
            label,
            gate.acquire_timeout_s,
        )
    except OSError as exc:
        logger.warning(
            "host admission unavailable for %s (%s); launching without a slot", label, exc
        )

    try:
        yield lease
    finally:
        if lease is not None:
            try:
                gate.release(lease)
            except OSError as exc:
                logger.warning("could not release host admission slot for %s: %s", label, exc)
