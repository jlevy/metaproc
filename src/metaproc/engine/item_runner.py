"""Per-item execution shapes: fan-out, and item-aligned chains.

The scheduling logic here is separated from what actually performs a step. A caller
supplies an *invoke* callable for that, so this module owns only the questions that are
about items (how many run at once, which of them may skip, what a failure does to its
siblings) and can be exercised without a process spec, a filesystem, or a CLI.

The two shapes differ in one thing. A fan-out maps one step over a roster. A chain maps
several steps over the same roster with item-scoped edges between them, so each item
walks the whole chain on its own rather than waiting at every step boundary.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence

# (step_id, item variables) -> succeeded. Async because a step is usually a subprocess
# or an agent call; the caller owns everything about how that happens.
StepInvoker = Callable[[str, dict[str, str]], Awaitable[bool]]

# (step_id, item variables) -> already finished, skip it. Used on resume so an item
# continues from where it stopped instead of redoing committed work.
StepCompletionCheck = Callable[[str, dict[str, str]], bool]


def _gate(max_concurrency: int | None, item_count: int) -> asyncio.Semaphore:
    """Bound in-flight items, defaulting to no bound beyond the roster itself."""
    limit = max_concurrency if max_concurrency and max_concurrency > 0 else item_count
    return asyncio.Semaphore(max(1, limit))


async def run_fan_out(
    *,
    item_contexts: Sequence[dict[str, str]],
    variables: dict[str, str],
    invoke: StepInvoker,
    step_id: str,
    max_concurrency: int | None = None,
    external_semaphore: asyncio.Semaphore | None = None,
) -> tuple[int, int]:
    """Run one step once per item. Returns (succeeded, total).

    Every item is awaited even after a sibling fails, so partial coverage stays
    readable: a caller that cancelled the rest would leave the per-item records
    ambiguous about which items were tried.
    """
    if not item_contexts:
        return (0, 0)

    gate = _gate(max_concurrency, len(item_contexts))

    async def _one(item_context: dict[str, str]) -> bool:
        item_vars = {**variables, **item_context}
        async with gate:
            if external_semaphore is not None:
                async with external_semaphore:
                    return await invoke(step_id, item_vars)
            return await invoke(step_id, item_vars)

    results = await asyncio.gather(*(_one(ctx) for ctx in item_contexts))
    return (sum(1 for ok in results if ok), len(results))


async def run_aligned_chain(
    *,
    chain: Sequence[str],
    item_contexts: Sequence[dict[str, str]],
    variables: dict[str, str],
    invoke: StepInvoker,
    is_done: StepCompletionCheck | None = None,
    max_concurrency: int | None = None,
    external_semaphore: asyncio.Semaphore | None = None,
) -> dict[str, tuple[int, int]]:
    """Run a chain of steps once per item. Returns (succeeded, reached) per step.

    Each item walks the chain independently, which is what an item-scoped edge means:
    item *k* of the second step waits on item *k* of the first and on nothing else, so a
    fast item is never held behind a slow stranger.

    Failure is item-scoped for the same reason. An item that fails stops its own walk
    and touches no sibling, so the chain finishes with partial coverage rather than
    blocking the graph. `reached` counts the items that actually arrived at a step, so a
    step's verdict is about what ran rather than treating an item that died earlier as
    that step's failure.
    """
    reached: dict[str, int] = dict.fromkeys(chain, 0)
    succeeded: dict[str, int] = dict.fromkeys(chain, 0)
    if not item_contexts:
        return {step_id: (0, 0) for step_id in chain}

    gate = _gate(max_concurrency, len(item_contexts))
    tally = asyncio.Lock()

    async def _walk(item_context: dict[str, str]) -> None:
        item_vars = {**variables, **item_context}
        for step_id in chain:
            if is_done is not None and is_done(step_id, item_vars):
                async with tally:
                    reached[step_id] += 1
                    succeeded[step_id] += 1
                continue
            async with tally:
                reached[step_id] += 1
            async with gate:
                if external_semaphore is not None:
                    async with external_semaphore:
                        ok = await invoke(step_id, item_vars)
                else:
                    ok = await invoke(step_id, item_vars)
            if not ok:
                return
            async with tally:
                succeeded[step_id] += 1

    await asyncio.gather(*(_walk(ctx) for ctx in item_contexts))
    return {step_id: (succeeded[step_id], reached[step_id]) for step_id in chain}
