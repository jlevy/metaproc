"""Chain detection for item-scoped edges.

`item_aligned_chains` decides where the level walk's step barrier may be replaced by a
per-item pipeline. Getting it wrong in the permissive direction joins unrelated work, so
most of these tests are about what it refuses.
"""

from __future__ import annotations

from typing import Literal

from metaproc.engine.graph import item_aligned_chains
from metaproc.models.plan import FanOut, ResolvedStep


def _step(
    step_id: str,
    *,
    needs: list[str] | None = None,
    source: str | None = "roster.md",
    align: Literal["same_key"] | None = None,
) -> ResolvedStep:
    fan_out = (
        FanOut(over="deps.roster", bind="item", source=source, align=align)
        if source is not None
        else None
    )
    return ResolvedStep(step_id=step_id, mode="code", needs=needs or [], fan_out=fan_out)


class TestChainDetection:
    def test_a_three_step_chain_is_found(self) -> None:
        steps = [
            _step("a"),
            _step("b", needs=["a"], align="same_key"),
            _step("c", needs=["b"], align="same_key"),
        ]
        assert item_aligned_chains(steps) == [["a", "b", "c"]]

    def test_no_align_means_no_chain(self) -> None:
        """The compatibility floor: an existing spec keeps step-scoped edges."""
        steps = [_step("a"), _step("b", needs=["a"]), _step("c", needs=["b"])]
        assert item_aligned_chains(steps) == []

    def test_a_chain_stops_at_a_step_with_an_outside_need(self) -> None:
        """That edge is genuinely step-scoped, so the barrier has to stay."""
        steps = [
            _step("a"),
            _step("b", needs=["a"], align="same_key"),
            _step("side"),
            _step("c", needs=["b", "side"], align="same_key"),
        ]
        assert item_aligned_chains(steps) == [["a", "b"]]

    def test_different_sources_do_not_align(self) -> None:
        """Matching keys across unrelated rosters is coincidence, not identity."""
        steps = [
            _step("a", source="left.md"),
            _step("b", needs=["a"], source="right.md", align="same_key"),
        ]
        assert item_aligned_chains(steps) == []

    def test_a_scalar_upstream_does_not_align(self) -> None:
        steps = [
            _step("a", source=None),
            _step("b", needs=["a"], align="same_key"),
        ]
        assert item_aligned_chains(steps) == []

    def test_a_fork_leaves_both_edges_step_scoped(self) -> None:
        """Two steps aligning to one upstream: neither can own the chain."""
        steps = [
            _step("a"),
            _step("b", needs=["a"], align="same_key"),
            _step("c", needs=["a"], align="same_key"),
        ]
        assert item_aligned_chains(steps) == []

    def test_chains_are_disjoint_and_maximal(self) -> None:
        steps = [
            _step("a"),
            _step("b", needs=["a"], align="same_key"),
            _step("x"),
            _step("y", needs=["x"], align="same_key"),
        ]
        chains = item_aligned_chains(steps)
        assert sorted(chains) == [["a", "b"], ["x", "y"]]
        flat = [sid for chain in chains for sid in chain]
        assert len(flat) == len(set(flat))

    def test_a_single_aligned_step_is_not_a_chain(self) -> None:
        """A chain needs an edge; one step has none."""
        assert item_aligned_chains([_step("a", align="same_key")]) == []
