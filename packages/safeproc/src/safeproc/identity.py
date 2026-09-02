"""Process identity and tree structure.

A PID alone names nothing for long: the kernel recycles it. An identity is a PID plus a
creation token that the platform guarantees differs between incarnations, and every
destructive action is fenced by revalidating that identity immediately before signalling.
An argv pattern may locate a candidate for observation; it never authorizes a signal.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessIdentity:
    """One incarnation of a process: its PID and a platform creation token.

    On Linux the token is the ``starttime`` field of ``/proc/<pid>/stat`` in clock ticks
    since boot. On macOS it is the process start time from the BSD process info. Both are
    compared for exact equality; a mismatch means the PID was recycled.
    """

    pid: int
    create_token: int

    def matches(self, record: ProcessRecord) -> bool:
        return record.pid == self.pid and record.create_token == self.create_token


@dataclass(frozen=True)
class ProcessTarget:
    """An existing root to monitor, identified by PID and optionally fenced by token.

    A target does not imply ownership, attachment, or admission. When ``create_token`` is
    ``None`` the monitor fences the target on its first observation and refuses to follow
    a recycled PID after that.
    """

    pid: int
    create_token: int | None = None
    label: str = ""


@dataclass(frozen=True)
class ProcessRecord:
    """One row of the process table as the platform reported it.

    ``rss_mb`` is what the table gives cheaply and is wrong in both directions under
    pressure; ``cost_mb`` prefers the platform's attributable cost, physical footprint on
    macOS or proportional set size on Linux, when a sample paid for it.
    """

    pid: int
    ppid: int
    uid: int
    state: str
    rss_mb: float
    age_s: float
    cmd: str
    create_token: int
    footprint_mb: float = 0.0

    @property
    def cost_mb(self) -> float:
        """Attributable cost when measured, otherwise RSS."""
        return self.footprint_mb or self.rss_mb

    @property
    def identity(self) -> ProcessIdentity:
        return ProcessIdentity(self.pid, self.create_token)

    @property
    def is_zombie(self) -> bool:
        return self.state.startswith("Z")


def descendants(root_pid: int, table: Iterable[ProcessRecord]) -> list[ProcessRecord]:
    """The root and everything below it, reconstructed from parent links.

    Zombies are excluded: they hold no address space, so counting one inflates the tree
    and offering one as a victim wastes a round.
    """
    rows = [row for row in table if not row.is_zombie]
    by_parent: dict[int, list[ProcessRecord]] = {}
    for row in rows:
        by_parent.setdefault(row.ppid, []).append(row)
    found = [row for row in rows if row.pid == root_pid]
    queue = list(found)
    while queue:
        current = queue.pop()
        children = by_parent.get(current.pid, [])
        found.extend(children)
        queue.extend(children)
    return found


def spawners(root_pid: int, tree: Sequence[ProcessRecord]) -> list[int]:
    """The root plus every tree member that has children: the processes that fork.

    A producer is usually multi-level. Freezing the root alone stops new lanes while
    every live orchestrator underneath keeps launching; the guard corpus recorded a tree
    growing by 10.9 GB during one root-only pause.
    """
    members = {row.pid for row in tree}
    parents = {row.ppid for row in tree if row.ppid in members}
    return [root_pid, *sorted(parents - {root_pid})]


def deepest_first(root_pid: int, table: Sequence[ProcessRecord]) -> list[int]:
    """Descendants of ``root_pid`` ordered so no kill orphans a process a later kill needs."""
    subtree = [row.pid for row in descendants(root_pid, table) if row.pid != root_pid]
    by_pid = {row.pid: row for row in table}
    depth: dict[int, int] = {}
    for member in subtree:
        distance, current = 0, by_pid.get(member)
        while current is not None and current.pid != root_pid and distance < 64:
            current = by_pid.get(current.ppid)
            distance += 1
        depth[member] = distance
    subtree.sort(key=lambda member: -depth.get(member, 0))
    return subtree


def fenced(identity: ProcessIdentity, table: Iterable[ProcessRecord]) -> ProcessRecord | None:
    """The live row for ``identity``, or ``None`` if it exited or the PID was recycled."""
    for row in table:
        if row.pid == identity.pid:
            return row if identity.matches(row) and not row.is_zombie else None
    return None


def find_by_pattern(
    pattern: str, table: Iterable[ProcessRecord], *, exclude_pids: Iterable[int] = ()
) -> ProcessRecord | None:
    """Locate a candidate root by argv fragment. Observation only; never an authority."""
    excluded = set(exclude_pids)
    for row in table:
        if row.pid in excluded or row.is_zombie:
            continue
        if pattern in row.cmd:
            return row
    return None
