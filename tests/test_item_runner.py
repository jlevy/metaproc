"""Per-item execution: concurrency, failure isolation, and resume.

These exercise the scheduling decisions without a process spec, a filesystem, or a CLI,
which is why the logic lives apart from what performs a step.
"""

from __future__ import annotations

import asyncio

from metaproc.engine.item_runner import (
    StepInvoker,
    effective_concurrency,
    run_aligned_chain,
    run_fan_out,
    with_retry,
)

ITEMS = [{"k": "a"}, {"k": "b"}, {"k": "c"}]


def _recording_invoke() -> tuple[list[tuple[str, str]], StepInvoker]:
    """An invoke that records every (step, item) call and always succeeds."""
    calls: list[tuple[str, str]] = []

    async def invoke(step_id: str, variables: dict[str, str]) -> bool:
        calls.append((step_id, variables["k"]))
        return True

    return calls, invoke


class TestEffectiveConcurrency:
    def test_the_smaller_limit_binds(self) -> None:
        """Both answer different questions, so neither may override the other."""
        assert effective_concurrency(10, 3, 100) == 3
        assert effective_concurrency(3, 10, 100) == 3

    def test_either_limit_alone_binds(self) -> None:
        assert effective_concurrency(4, None, 100) == 4
        assert effective_concurrency(None, 4, 100) == 4

    def test_with_no_limit_the_roster_is_the_bound(self) -> None:
        assert effective_concurrency(None, None, 7) == 7

    def test_never_resolves_below_one(self) -> None:
        """An empty roster must not produce a semaphore that blocks forever."""
        assert effective_concurrency(None, None, 0) == 1


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

    def test_a_step_ceiling_bounds_below_the_run_cap(self) -> None:
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
                max_concurrency=10,
                step_concurrency=1,
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

    def test_each_step_gets_its_own_ceiling(self) -> None:
        """A tight ceiling on one stage must not throttle the others.

        Step two dwells far longer than step one, so items released one at a time by
        step one's ceiling still accumulate in step two. With a uniform dwell they
        would not, and the test would pass on arithmetic rather than on the gate.
        """
        peaks: dict[str, int] = {"one": 0, "two": 0}
        active: dict[str, int] = {"one": 0, "two": 0}

        async def invoke(step_id: str, variables: dict[str, str]) -> bool:
            active[step_id] += 1
            peaks[step_id] = max(peaks[step_id], active[step_id])
            await asyncio.sleep(0.01 if step_id == "one" else 0.08)
            active[step_id] -= 1
            return True

        asyncio.run(
            run_aligned_chain(
                chain=["one", "two"],
                item_contexts=ITEMS,
                variables={},
                invoke=invoke,
                step_concurrency=lambda step_id: 1 if step_id == "one" else None,
            )
        )
        assert peaks["one"] == 1
        assert peaks["two"] > 1

    def test_an_empty_roster_reports_every_step_as_untouched(self) -> None:
        calls, invoke = _recording_invoke()
        tallies = asyncio.run(
            run_aligned_chain(chain=["one", "two"], item_contexts=[], variables={}, invoke=invoke)
        )
        assert tallies == {"one": (0, 0), "two": (0, 0)}
        assert calls == []


class TestWithRetry:
    """Retry composes around invocation; the budget is per step."""

    @staticmethod
    def _counting(fails_before_success: int) -> tuple[list[str], StepInvoker]:
        seen: list[str] = []

        async def invoke(step_id: str, variables: dict[str, str]) -> bool:
            seen.append(step_id)
            return len([s for s in seen if s == step_id]) > fails_before_success

        return seen, invoke

    @staticmethod
    def _budget(attempts: int):
        return lambda step_id: (attempts, lambda _attempt: 0.0)

    def test_a_step_with_no_budget_is_invoked_once(self) -> None:
        """The behavior of every spec that declares nothing."""
        seen, invoke = self._counting(fails_before_success=99)
        wrapped = with_retry(invoke, should_retry=lambda *_: True, budget_for=lambda _s: None)
        assert asyncio.run(wrapped("s", {})) is False
        assert seen == ["s"]

    def test_a_retryable_failure_is_attempted_again(self) -> None:
        seen, invoke = self._counting(fails_before_success=2)
        wrapped = with_retry(
            invoke, should_retry=lambda *_: True, budget_for=self._budget(3), sleep=_no_sleep
        )
        assert asyncio.run(wrapped("s", {})) is True
        assert len(seen) == 3

    def test_a_non_retryable_failure_stops_immediately(self) -> None:
        """The declaration decides, not the budget: a fail verdict spends one attempt."""
        seen, invoke = self._counting(fails_before_success=99)
        wrapped = with_retry(
            invoke, should_retry=lambda *_: False, budget_for=self._budget(5), sleep=_no_sleep
        )
        assert asyncio.run(wrapped("s", {})) is False
        assert len(seen) == 1

    def test_the_budget_bounds_a_persistently_failing_step(self) -> None:
        seen, invoke = self._counting(fails_before_success=99)
        wrapped = with_retry(
            invoke, should_retry=lambda *_: True, budget_for=self._budget(3), sleep=_no_sleep
        )
        assert asyncio.run(wrapped("s", {})) is False
        assert len(seen) == 3

    def test_each_step_gets_its_own_budget(self) -> None:
        """A chain runs several steps; one step's policy must not govern another."""
        seen, invoke = self._counting(fails_before_success=99)
        budgets = {"generous": (4, lambda _a: 0.0), "strict": (1, lambda _a: 0.0)}
        wrapped = with_retry(
            invoke,
            should_retry=lambda *_: True,
            budget_for=budgets.get,
            sleep=_no_sleep,
        )
        asyncio.run(wrapped("generous", {}))
        asyncio.run(wrapped("strict", {}))
        assert seen.count("generous") == 4
        assert seen.count("strict") == 1

    def test_the_delay_schedule_is_consulted_per_attempt(self) -> None:
        slept: list[float] = []

        async def sleep(seconds: float) -> None:
            slept.append(seconds)

        _seen, invoke = self._counting(fails_before_success=99)
        wrapped = with_retry(
            invoke,
            should_retry=lambda *_: True,
            budget_for=lambda _s: (4, lambda attempt: float(attempt) * 2),
            sleep=sleep,
        )
        asyncio.run(wrapped("s", {}))
        assert slept == [2.0, 4.0, 6.0]


async def _no_sleep(_seconds: float) -> None:
    return None
