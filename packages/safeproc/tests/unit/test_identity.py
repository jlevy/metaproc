from __future__ import annotations

from safeproc.identity import (
    ProcessIdentity,
    deepest_first,
    descendants,
    fenced,
    find_by_pattern,
    spawners,
)
from tests.conftest import row, tree_table


def test_descendants_walks_the_tree_and_drops_zombies() -> None:
    tree = descendants(100, tree_table())
    pids = {r.pid for r in tree}
    assert pids == {100, 101, 102, 103, 104, 105, 106, 107}
    assert 201 not in pids
    assert 200 not in pids


def test_spawners_are_every_non_leaf_not_just_the_root() -> None:
    tree = descendants(100, tree_table())
    assert spawners(100, tree) == [100, 101, 106]


def test_deepest_first_never_orphans_a_later_target() -> None:
    order = deepest_first(100, tree_table())
    assert order.index(107) < order.index(106)
    assert all(order.index(pid) < order.index(101) for pid in (102, 103, 104, 105))
    assert 100 not in order


def test_fenced_rejects_a_recycled_pid() -> None:
    table = tree_table()
    identity = ProcessIdentity(102, 102 * 1000)
    assert fenced(identity, table) is not None
    recycled = [row(102, 101, token=999) if r.pid == 102 else r for r in table]
    assert fenced(identity, recycled) is None


def test_fenced_treats_a_zombie_as_gone() -> None:
    assert fenced(ProcessIdentity(201, 201 * 1000), tree_table()) is None


def test_find_by_pattern_locates_but_excludes_self() -> None:
    table = tree_table()
    found = find_by_pattern("orchestrator", table)
    assert found is not None and found.pid == 101
    assert find_by_pattern("orchestrator", table, exclude_pids=[101]) is None
    assert find_by_pattern("no such thing", table) is None
