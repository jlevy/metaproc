"""Per-item execution: concurrency, failure isolation, and resume.

These exercise the scheduling decisions without a process spec, a filesystem, or a CLI,
which is why the logic lives apart from what performs a step.
"""

from __future__ import annotations

import asyncio

from metaproc.engine.item_runner import StepInvoker, run_aligned_chain, run_fan_out

ITEMS = [{"k": "a"}, {"k": "b"}, {"k": "c"}]


def _recording_invoke() -> tuple[list[tuple[str, str]], StepInvoker]:
    """An invoke that records every (step, item) call and always succeeds."""
    calls: list[tuple[str, str]] = []

    async def invoke(step_id: str, variables: dict[str, str]) -> bool:
        calls.append((step_id, variables["k"]))
        return True

    return calls, invoke


class TestRunFanOut:
    def test_every_item_is_invoked_once(self) -> None:
        calls, invoke = _recording_invoke()
        result = asyncio.run(
            run_fan_out(item_contexts=ITEMS, variables={}, invoke=invoke, step_id="s")
        )
        assert result == (3, 3)
        assert sorted(call[1] for call in calls) == ["a", "b", "c"]

    def test_a_failing_item_does_not_cancel_its_siblings(self) -> None:
        """Cancelling the rest would leave which items were tried ambiguous."""
        seen: list[str] = []

        async def invoke(step_id: str, variables: dict[str, str]) -> bool:
            seen.append(variables["k"])
            return variables["k"] != "b"

        result = asyncio.run(
            run_fan_out(item_contexts=ITEMS, variables={}, invoke=invoke, step_id="s")
        )
        assert result == (2, 3)
        assert sorted(seen) == ["a", "b", "c"]

    def test_concurrency_is_bounded(self) -> None:
        in_flight = 0
        peak = 0

        async def invoke(step_id: str, variables: dict[str, str]) -> bool:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return True

        asyncio.run(
            run_fan_out(
                item_contexts=ITEMS,
                variables={},
                invoke=invoke,
                step_id="s",
                max_concurrency=1,
            )
        )
        assert peak == 1

    def test_an_empty_roster_is_not_a_failure(self) -> None:
        calls, invoke = _recording_invoke()
        result = asyncio.run(
            run_fan_out(item_contexts=[], variables={}, invoke=invoke, step_id="s")
        )
        assert result == (0, 0)
        assert calls == []


class TestRunAlignedChain:
    def test_each_item_walks_the_whole_chain_in_order(self) -> None:
        calls, invoke = _recording_invoke()
        tallies = asyncio.run(
            run_aligned_chain(
                chain=["one", "two"], item_contexts=ITEMS, variables={}, invoke=invoke
            )
        )
        assert tallies == {"one": (3, 3), "two": (3, 3)}
        for item in ("a", "b", "c"):
            assert calls.index(("one", item)) < calls.index(("two", item))

    def test_an_item_failing_stops_only_its_own_walk(self) -> None:
        seen: list[tuple[str, str]] = []

        async def invoke(step_id: str, variables: dict[str, str]) -> bool:
            seen.append((step_id, variables["k"]))
            return not (step_id == "one" and variables["k"] == "b")

        tallies = asyncio.run(
            run_aligned_chain(
                chain=["one", "two"], item_contexts=ITEMS, variables={}, invoke=invoke
            )
        )
        assert tallies["one"] == (2, 3)
        assert tallies["two"] == (2, 2)
        assert ("two", "b") not in seen

    def test_a_step_already_done_for_an_item_is_skipped(self) -> None:
        """Resume: an item continues where it stopped rather than redoing work."""
        calls, invoke = _recording_invoke()

        def is_done(step_id: str, variables: dict[str, str]) -> bool:
            return step_id == "one" and variables["k"] == "a"

        tallies = asyncio.run(
            run_aligned_chain(
                chain=["one", "two"],
                item_contexts=ITEMS,
                variables={},
                invoke=invoke,
                is_done=is_done,
            )
        )
        assert ("one", "a") not in calls
        assert ("two", "a") in calls
        assert tallies["one"] == (3, 3)

    def test_items_overlap_across_steps(self) -> None:
        """The property a level walk cannot express."""
        active: set[tuple[str, str]] = set()
        overlapped = False

        async def invoke(step_id: str, variables: dict[str, str]) -> bool:
            nonlocal overlapped
            active.add((step_id, variables["k"]))
            await asyncio.sleep(0.05 if variables["k"] == "a" else 0.01)
            steps_active = {step for step, _ in active}
            if {"one", "two"} <= steps_active:
                overlapped = True
            active.discard((step_id, variables["k"]))
            return True

        asyncio.run(
            run_aligned_chain(
                chain=["one", "two"], item_contexts=ITEMS, variables={}, invoke=invoke
            )
        )
        assert overlapped

    def test_an_empty_roster_reports_every_step_as_untouched(self) -> None:
        calls, invoke = _recording_invoke()
        tallies = asyncio.run(
            run_aligned_chain(chain=["one", "two"], item_contexts=[], variables={}, invoke=invoke)
        )
        assert tallies == {"one": (0, 0), "two": (0, 0)}
        assert calls == []
