"""psutil-backed CPU / RSS sampler for code-step resource attribution.

Code-mode steps such as ``normalize-items`` (the motivating example
for this sampler) spend most of their wall-clock time in a
``ThreadPoolExecutor`` driving subprocess work, so the run-pool's
``ProcessExitEvent.peak_rss_bytes`` is too coarse — it only reports the
RSS at exit, missing transient peaks. This module provides a lightweight
sampler that records CPU% and RSS at a fixed cadence over the lifetime
of a step (and its child subprocesses), then emits one `SampleEvent`
per wake-up into a `ResourceEventLogger` so the joiner can roll those
samples up under the owning step / process / item node.

- No flamegraphs: operators only need enough signal to explain whether
  a step is CPU-bound, memory-bound, or network-bound.
- Sampling happens in a daemon thread so the main step thread is not
  perturbed.
- Sampler tolerates child processes that exit during the window
  (psutil raises ``NoSuchProcess`` then) without crashing the step.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Self

import psutil

from metaproc.logutil.resource_events import ResourceEventLogger
from metaproc.models.resources import (
    HierarchyRef,
    Metrics,
    SampleEvent,
    SourceRef,
)

log = logging.getLogger(__name__)

DEFAULT_SAMPLE_INTERVAL_S = 1.0


@dataclass
class SampleStats:
    """Aggregate of every wake-up across the sampler's lifetime."""

    sample_count: int = 0
    cpu_pct_sum: float = 0.0
    cpu_pct_max: float = 0.0
    rss_bytes_sum: int = 0
    rss_bytes_max: int = 0
    samples: list[tuple[datetime, float, int]] = field(default_factory=list)

    @property
    def cpu_pct_avg(self) -> float:
        if self.sample_count == 0:
            return 0.0
        return self.cpu_pct_sum / self.sample_count

    @property
    def rss_bytes_avg(self) -> float:
        if self.sample_count == 0:
            return 0.0
        return self.rss_bytes_sum / self.sample_count


class PsutilSampler:
    """Daemon-thread sampler that records CPU% and RSS at a fixed cadence.

    Use as a context manager around a code-step's body. While the
    sampler is open it walks the current process tree on each wake-up
    (parent + children, all states), records `(timestamp, cpu_pct, rss)`
    summed across the tree, and writes a `SampleEvent` to the supplied
    `ResourceEventLogger` (when one is provided) so the resource joiner
    can roll the samples up under the owning step / item node.

    For in-process handlers, `exclude_preexisting_children` snapshots the
    root's direct children on entry and omits those entire subtrees. New
    direct children remain attributable to the handler and are sampled.
    """

    def __init__(
        self,
        *,
        hierarchy: HierarchyRef,
        source: SourceRef,
        logger: ResourceEventLogger | None = None,
        interval_s: float = DEFAULT_SAMPLE_INTERVAL_S,
        pid: int | None = None,
        exclude_preexisting_children: bool = False,
    ) -> None:
        if interval_s <= 0:
            raise ValueError(f"interval_s must be > 0, got {interval_s!r}")
        self._hierarchy = hierarchy
        self._source = source
        self._logger = logger
        self._interval_s = interval_s
        self._pid = pid
        self._exclude_preexisting_children = exclude_preexisting_children
        self._excluded_child_pids: frozenset[int] = frozenset()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.stats = SampleStats()

    def __enter__(self) -> Self:
        self._stop_event.clear()
        try:
            root = psutil.Process(self._pid) if self._pid is not None else psutil.Process()
            if self._exclude_preexisting_children:
                self._excluded_child_pids = frozenset(
                    child.pid for child in root.children(recursive=False)
                )
            self._prime_process_tree(root)
        except psutil.Error:
            log.debug("PsutilSampler could not attach to process tree", exc_info=True)
            return self

        # Capture RSS synchronously so a short-lived child cannot exit before
        # the daemon thread gets its first scheduling opportunity.
        self._take_sample(root)
        self._thread = threading.Thread(
            target=self._sample_loop,
            args=(root,),
            name="psutil-sampler",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=self._interval_s * 5)
            self._thread = None

    def _process_tree(self, root: psutil.Process) -> list[psutil.Process]:
        if not self._excluded_child_pids:
            return [root, *root.children(recursive=True)]

        processes = [root]
        for child in root.children(recursive=False):
            if child.pid in self._excluded_child_pids:
                continue
            processes.append(child)
            try:
                processes.extend(child.children(recursive=True))
            except psutil.Error:
                continue
        return processes

    def _prime_process_tree(self, root: psutil.Process) -> None:
        # Prime cpu_percent — psutil needs an initial call to seed the delta.
        for process in self._process_tree(root):
            try:
                process.cpu_percent(interval=None)
            except psutil.Error:
                continue

    def _sample_loop(self, root: psutil.Process) -> None:
        while not self._stop_event.wait(self._interval_s):
            self._take_sample(root)

        # One final sample on shutdown so short steps still record at least once.
        self._take_sample(root)

    def _take_sample(self, root: psutil.Process) -> None:
        try:
            procs = self._process_tree(root)
        except psutil.Error:
            return

        cpu_total = 0.0
        rss_total = 0
        for proc in procs:
            try:
                cpu_total += proc.cpu_percent(interval=None)
                rss_total += proc.memory_info().rss
            except psutil.Error:
                # Subprocess may have exited between enumerate and read; skip.
                continue

        ts = datetime.now(UTC)
        self.stats.sample_count += 1
        self.stats.cpu_pct_sum += cpu_total
        self.stats.cpu_pct_max = max(self.stats.cpu_pct_max, cpu_total)
        self.stats.rss_bytes_sum += rss_total
        self.stats.rss_bytes_max = max(self.stats.rss_bytes_max, rss_total)
        self.stats.samples.append((ts, cpu_total, rss_total))

        if self._logger is None:
            return

        try:
            self._logger.write(
                SampleEvent(
                    ts=ts,
                    hierarchy=self._hierarchy,
                    source=self._source,
                    metrics=Metrics(
                        cpu_pct_avg=cpu_total,
                        cpu_pct_max=cpu_total,
                        rss_bytes_avg=float(rss_total),
                        rss_bytes_max=rss_total,
                    ),
                )
            )
        except Exception:  # noqa: BLE001
            # Sampler must never crash the step that owns it; log and move on.
            log.debug("PsutilSampler failed to emit SampleEvent", exc_info=True)
