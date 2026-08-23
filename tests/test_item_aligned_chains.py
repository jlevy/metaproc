"""Chain detection for item-scoped edges.

`item_aligned_chains` decides where the level walk's step barrier may be replaced by a
per-item pipeline. Getting it wrong in the permissive direction joins unrelated work, so
most of these tests are about what it refuses.
"""

from __future__ import annotations

from pathlib import Path
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


class TestAChainHeadDoesNotSkipItsChain:
    """A complete head must not short-circuit the members behind it.

    Chain execution hangs off the head in `run_process`'s step loop, so the
    completed-step skip and the chain dispatch are two branches on the same step. If the
    skip wins, every member is skipped with the head whatever its own state, and a resume
    repairing one item of one member exits clean having done nothing -- silently, because
    nothing errors and the run reports success.

    Asserted against the source rather than by executing a resume: what protects the
    behaviour is that the guard exists next to the skip, and a source assertion says so
    without standing up a nine-step run tree. The behaviour itself was verified by hand on
    a real cohort, where deleting one ticker's record for the sixth member changed nothing
    before the fix and re-ran exactly that item after it.
    """

    @staticmethod
    def _source() -> str:
        return (
            Path(__file__).parents[1] / "src" / "metaproc" / "commands" / "run_process.py"
        ).read_text()

    def test_the_completed_step_skip_excludes_chain_heads(self) -> None:
        source = self._source()
        marker = "and step_id not in _chain_head_of"
        assert marker in source, (
            "the completed-step skip no longer excludes chain heads; a complete head "
            "will skip its whole chain and resume cannot re-run a failed member"
        )
        skip_at = source.index("already completed — skipping")
        guard_at = source.index(marker)
        assert guard_at < skip_at, "the guard must sit on the branch that emits the skip"

    def test_the_chain_dispatch_still_keys_on_the_head(self) -> None:
        """The guard is only needed because dispatch hangs off the head; if that ever
        changes, this test should fail and the guard be reconsidered rather than kept."""
        assert "_chain_head_of.get(step_id)" in self._source()
