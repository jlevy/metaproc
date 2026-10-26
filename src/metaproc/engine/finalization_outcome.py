"""The run's durable record of how its latest launch ended.

``resources.json`` and ``resource-usage-summary.md`` are projections: each is replaced
whole by whichever writer publishes last, and either can be an earlier launch's while the
other is this one's. Neither can be the authority on how a run ended, because a signal
can interrupt a publication between the two, and because ``metaproc status`` rebuilds
both from evidence it read before the run's own orchestrator published.

So the causal outcome is recorded on its own, in ``.state/finalization-outcome.json``.
The record holds one thing and nothing else: **what a launch of this run observed.**

- The run's own orchestrator writes it, and may replace what an earlier launch wrote. It
  is written first in every publication, so an interrupted publication still leaves the
  outcome, and a hard-stop signal writes it before anything else.
- Recovery — ``metaproc status``, ``metaproc resource-report``, the Metabrowser
  sidekick — never writes it. Recovery infers an outcome from persisted evidence rather
  than observing one, and an inference that outlived the evidence it was drawn from
  would be worse than no record at all.
- Every recovery path reads it to choose the outcome it publishes, so a rebuilt summary
  keeps the state and the signal name the launch recorded. A run with no record — one
  killed by SIGKILL, or finalized by a Metaproc from before the record — is inferred
  from evidence exactly as it was before.

The record holds a :class:`~metaproc.models.resource_budget.ResourceFinalization`, the
same shape the projections carry, so a reader needs no second contract.
"""

from __future__ import annotations

import logging
from pathlib import Path

from strif import atomic_write_text

from metaproc.models.resource_budget import ResourceFinalization
from metaproc.paths import FINALIZATION_OUTCOME_FILE, STATE_DIR

log = logging.getLogger(__name__)


def finalization_outcome_path(run_dir: Path) -> Path:
    """Return ``<run_dir>/.state/finalization-outcome.json``."""
    return run_dir / STATE_DIR / FINALIZATION_OUTCOME_FILE


def read_finalization_outcome(run_dir: Path) -> ResourceFinalization | None:
    """Return the run's recorded causal outcome, or ``None`` if none reads."""
    path = finalization_outcome_path(run_dir)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return ResourceFinalization.model_validate_json(raw)
    except Exception:  # noqa: BLE001 - an unreadable record is no record
        log.debug("could not read the finalization outcome at %s", path, exc_info=True)
        return None


def write_finalization_outcome(run_dir: Path, finalization: ResourceFinalization) -> None:
    """Record how the run's latest launch ended, replacing any earlier launch's record.

    Only a launch that observed its own outcome calls this. Call it holding the run's
    finalization lock, except from a hard-stop signal handler, which can take no lock and
    writes the newest causal fact there is.
    """
    atomic_write_text(
        finalization_outcome_path(run_dir),
        finalization.model_dump_json(exclude_none=False),
        make_parents=True,
    )


def finalization_outcome_snapshot(run_dir: Path) -> bytes | None:
    """Return the bytes of the run's outcome record, or ``None`` if absent.

    A recovery writer takes this before it reads the run's evidence and compares it again
    under the finalization lock, so a launch that recorded its outcome meanwhile — a
    hard-stop signal's, which takes no lock — is never published over.
    """
    try:
        return finalization_outcome_path(run_dir).read_bytes()
    except OSError:
        return None
