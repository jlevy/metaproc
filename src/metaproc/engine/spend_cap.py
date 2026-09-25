"""Run spend cap and the mapped-step dispatch breaker.

A run launched with ``--max-spend-usd`` carries one cap on its measured list cost, shared
by every scope it enters. Three mechanisms read it:

1. Before a priced mapped step dispatches anything, ``check_worst_case`` refuses when the
   spend already measured plus the step's worst case exceeds the cap. The worst case
   counts only the items the step will actually run after reuse, times the most attempts
   one item can make, times the step's declared per-item price (``spend:``).
2. While items run, ``SpendLedger.observe`` adds the list cost of each finished task's
   logs, and a ``MappedDispatch`` gate stops starting new items once the measured spend
   reaches the cap. Items already running finish; the rest keep no task record and stay
   pending for a resume.
3. The same gate trips a mapped step's ``for_each.breaker`` once too large a share of
   the items it finished in this invocation failed.

Measured spend is list cost exactly as resource projection computes it
(``log_list_cost_usd``): an estimate at list prices, and a lower bound when a log's model
has no price, which the ledger counts as ``unpriced_logs``. The ledger records its total
in ``.state/spend-ledger.yaml`` as it changes, so a resume on a machine that lacks the
earlier logs still counts their spend: at launch it takes the larger of that record and
a rescan of the logs present.

The cap is cumulative over the run's life. A run stopped at its cap and resumed with the
same cap refuses again before dispatching anything; a higher cap lets it continue.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field
from strif import atomic_write_text

from metaproc.engine.resource_rollup import discover_log_files, parse_log_file
from metaproc.errors import CLIError
from metaproc.io import from_yaml_string, logical_path, to_yaml_string
from metaproc.logutil.resource_event_extract import log_list_cost_usd
from metaproc.models.authored import DispatchBreaker
from metaproc.models.dispatch_stop import DispatchStop, DispatchStopReason
from metaproc.paths import RESOURCE_EVENTS_FILE, spend_ledger_file

SPEND_LEDGER_CONTRACT = "metaproc:SpendLedger/0.1"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ── Stop signal ─────────────────────────────────────────────────────────


class DispatchStopped(CLIError):
    """A mapped step refused or stopped dispatch under the spend cap or its breaker."""

    def __init__(self, stop: DispatchStop) -> None:
        super().__init__(stop.message)
        self.stop: DispatchStop = stop


# ── Ledger ──────────────────────────────────────────────────────────────


class SpendLedgerRecord(BaseModel):
    """``.state/spend-ledger.yaml``: the run's cap and the spend measured against it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: Literal["metaproc:SpendLedger/0.1"] = Field(
        default="metaproc:SpendLedger/0.1", alias="schema"
    )
    cap_usd: float = Field(gt=0)
    measured_usd: float = Field(ge=0)
    unpriced_logs: int = Field(default=0, ge=0)
    updated_at: str


def read_spend_ledger(run_dir: Path) -> SpendLedgerRecord | None:
    """Return the recorded ledger, or ``None`` when the run never had a cap."""
    path = spend_ledger_file(run_dir)
    if not path.is_file():
        return None
    try:
        raw = from_yaml_string(path.read_text(encoding="utf-8"))
        return SpendLedgerRecord.model_validate(raw)
    except ValueError as exc:
        raise CLIError(f"Corrupt spend ledger {path}: {exc}") from exc


@dataclass
class _LogCost:
    stamp: tuple[int, int]
    cost_usd: float
    unpriced: bool


@dataclass
class SpendLedger:
    """Measured list cost of one run, keyed by log file so a rescan never double counts.

    ``carried_usd`` is spend the run's record holds that no log present on this machine
    accounts for, such as a resume from a fresh checkout. Each log is counted once under
    its logical path, and a changed log (compaction, a longer attempt) replaces its own
    earlier contribution.
    """

    run_dir: Path
    cap_usd: float
    carried_usd: float = 0.0
    _logs: dict[str, _LogCost] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def open(cls, run_dir: Path, *, cap_usd: float) -> SpendLedger:
        """Open the run's ledger: rescan its logs, carry any recorded excess, record it."""
        if cap_usd <= 0:
            raise CLIError(f"--max-spend-usd must be positive, got {cap_usd:g}")
        recorded = read_spend_ledger(run_dir)
        ledger = cls(run_dir=run_dir, cap_usd=cap_usd)
        _ = ledger.scan(run_dir)
        if recorded is not None:
            ledger.carried_usd = max(0.0, recorded.measured_usd - ledger.scanned_usd)
        ledger.persist()
        return ledger

    @property
    def scanned_usd(self) -> float:
        return sum(entry.cost_usd for entry in self._logs.values())

    @property
    def measured_usd(self) -> float:
        return self.carried_usd + self.scanned_usd

    @property
    def unpriced_logs(self) -> int:
        return sum(entry.unpriced for entry in self._logs.values())

    def exhausted(self) -> bool:
        return self.measured_usd >= self.cap_usd

    def scan(self, root: Path) -> bool:
        """Account for every new or changed log under *root*; return whether any was."""
        changed = False
        for path in discover_log_files(root):
            if path.name.startswith(RESOURCE_EVENTS_FILE):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            key = str(logical_path(path).resolve())
            stamp = (stat.st_size, stat.st_mtime_ns)
            with self._lock:
                current = self._logs.get(key)
            if current is not None and current.stamp == stamp:
                continue
            log_file, _events = parse_log_file(path)
            if log_file is None:
                continue
            cost = log_list_cost_usd(log_file)
            stats = log_file.usage_stats
            unpriced = cost is None and stats is not None and stats.has_token_usage
            with self._lock:
                self._logs[key] = _LogCost(stamp, float(cost or 0.0), unpriced)
            changed = True
        return changed

    def observe(self, root: Path) -> float:
        """Count the logs a finished task left under *root*, record the total, return it."""
        if self.scan(root):
            self.persist()
        return self.measured_usd

    def persist(self) -> None:
        record = SpendLedgerRecord(
            cap_usd=self.cap_usd,
            measured_usd=round(self.measured_usd, 6),
            unpriced_logs=self.unpriced_logs,
            updated_at=_now_iso(),
        )
        with self._lock:
            atomic_write_text(
                spend_ledger_file(self.run_dir),
                to_yaml_string(record.model_dump(mode="json", by_alias=True)),
                make_parents=True,
            )


# ── Pre-dispatch check ──────────────────────────────────────────────────


@dataclass(frozen=True)
class SpendCeiling:
    """The most one mapped step can spend: items x attempts x per-item price."""

    actionable_items: int
    max_attempts: int
    per_item_usd: float

    @property
    def usd(self) -> float:
        return self.actionable_items * self.max_attempts * self.per_item_usd


def check_worst_case(
    ledger: SpendLedger,
    *,
    step_id: str,
    worst: SpendCeiling,
    source: str,
) -> None:
    """Refuse, before any item starts, a step whose worst case would pass the cap."""
    measured = ledger.measured_usd
    total = measured + worst.usd
    if total <= ledger.cap_usd:
        return
    message = (
        f"Step '{step_id}' refused to dispatch {worst.actionable_items} items: the worst "
        f"case is {worst.actionable_items} items x {worst.max_attempts} attempts x "
        f"{worst.per_item_usd:g} USD = {worst.usd:.2f} USD, and with {measured:.2f} USD "
        f"already measured that is {total:.2f} USD, above the {ledger.cap_usd:.2f} USD "
        f"spend cap. Raise --max-spend-usd to at least {total:.2f} to continue. "
        f"Per-item price: {source}"
    )
    raise DispatchStopped(
        DispatchStop(
            reason="spend-cap",
            phase="pre-dispatch",
            step_id=step_id,
            not_dispatched=worst.actionable_items,
            message=message,
            stopped_at=_now_iso(),
            cap_usd=ledger.cap_usd,
            measured_usd=round(measured, 6),
            worst_case_usd=round(worst.usd, 6),
            actionable_items=worst.actionable_items,
            max_attempts=worst.max_attempts,
            per_item_usd=worst.per_item_usd,
        )
    )


# ── In-run gate ─────────────────────────────────────────────────────────


@dataclass
class MappedDispatch:
    """Decide, item by item, whether a mapped step may start another item.

    ``run_fan_out`` calls ``admit`` once an item holds every concurrency gate and just
    before it would run, and ``record`` with the item's result. The first trip of either
    condition is kept, so the reported numbers are the ones that stopped dispatch.
    """

    step_id: str
    ledger: SpendLedger | None = None
    breaker: DispatchBreaker | None = None
    finished: int = 0
    failed: int = 0
    not_dispatched: int = 0
    _trip: DispatchStopReason | None = None
    _trip_numbers: dict[str, float | int] = field(default_factory=dict)

    def admit(self) -> bool:
        if self._trip is None:
            self._evaluate()
        if self._trip is not None:
            self.not_dispatched += 1
            return False
        return True

    def record(self, *, succeeded: bool) -> None:
        self.finished += 1
        if not succeeded:
            self.failed += 1

    def _evaluate(self) -> None:
        if self.ledger is not None and self.ledger.exhausted():
            self._trip = "spend-cap"
            self._trip_numbers = {"measured_usd": round(self.ledger.measured_usd, 6)}
            return
        breaker = self.breaker
        if (
            breaker is not None
            and self.finished >= breaker.min_finished
            and self.failed / self.finished > breaker.max_failure_fraction
        ):
            self._trip = "failure-rate"
            self._trip_numbers = {"finished": self.finished, "failed": self.failed}

    @property
    def stop(self) -> DispatchStop | None:
        """The stop record once dispatch has stopped, with the final undispatched count."""
        if self._trip is None:
            return None
        if self._trip == "spend-cap":
            assert self.ledger is not None
            measured = float(self._trip_numbers["measured_usd"])
            return DispatchStop(
                reason="spend-cap",
                phase="in-run",
                step_id=self.step_id,
                not_dispatched=self.not_dispatched,
                message=(
                    f"Step '{self.step_id}' stopped dispatching: measured spend "
                    f"{measured:.2f} USD reached the {self.ledger.cap_usd:.2f} USD spend "
                    f"cap with {self.not_dispatched} items not dispatched; items already "
                    "running finished. Raise --max-spend-usd to continue."
                ),
                stopped_at=_now_iso(),
                cap_usd=self.ledger.cap_usd,
                measured_usd=measured,
            )
        assert self.breaker is not None
        finished = int(self._trip_numbers["finished"])
        failed = int(self._trip_numbers["failed"])
        return DispatchStop(
            reason="failure-rate",
            phase="in-run",
            step_id=self.step_id,
            not_dispatched=self.not_dispatched,
            message=(
                f"Step '{self.step_id}' stopped dispatching: {failed} of {finished} "
                f"finished items failed ({failed / finished:.0%}, above "
                f"{self.breaker.max_failure_fraction:.0%} after at least "
                f"{self.breaker.min_finished}), with {self.not_dispatched} items not "
                "dispatched; items already running finished."
            ),
            stopped_at=_now_iso(),
            finished=finished,
            failed=failed,
            max_failure_fraction=self.breaker.max_failure_fraction,
            min_finished=self.breaker.min_finished,
        )

    def raise_if_stopped(self) -> None:
        stop = self.stop
        if stop is not None:
            raise DispatchStopped(stop)


__all__ = [
    "SPEND_LEDGER_CONTRACT",
    "DispatchStop",
    "DispatchStopReason",
    "DispatchStopped",
    "MappedDispatch",
    "SpendLedger",
    "SpendLedgerRecord",
    "SpendCeiling",
    "check_worst_case",
    "read_spend_ledger",
]
