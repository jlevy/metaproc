"""Adaptive asyncio semaphore with dynamically adjustable capacity.

Increasing capacity releases extra tokens so blocked acquirers can proceed.
Decreasing capacity reduces the limit for future acquires without preempting
current holders — existing work continues, but new work waits until enough
slots are freed.
"""

from __future__ import annotations

import asyncio
from typing import Self


class AdaptiveSemaphore:
    """An asyncio semaphore whose capacity can be changed at runtime.

    Unlike ``asyncio.Semaphore``, this supports ``set_capacity()`` which
    adjusts the maximum concurrency. The implementation wraps a standard
    semaphore and issues extra releases or consumes tokens on resize.
    """

    def __init__(self, initial: int = 1) -> None:
        if initial < 1:
            raise ValueError(f"initial capacity must be >= 1, got {initial}")
        self._capacity = initial
        self._sem = asyncio.Semaphore(initial)
        # Track how many tokens are currently held (acquired but not released).
        self._held = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def held(self) -> int:
        """Number of tokens currently held (acquired but not released)."""
        return self._held

    @property
    def available(self) -> int:
        """Number of tokens available for immediate acquire."""
        return self._capacity - self._held

    def set_capacity(self, new_capacity: int) -> None:
        """Adjust the semaphore capacity.

        If *new_capacity* > current **and** held < new_capacity, extra tokens
        are released to fill the gap (unblocking waiters if any).  If the pool
        is already over the new capacity (held >= new_capacity), no tokens are
        released — the over-capacity drains naturally via release() absorbing
        tokens.

        If *new_capacity* < current, available (unheld) tokens are consumed so
        future acquires block.  Held tokens drain via release() absorption.
        """
        if new_capacity < 1:
            raise ValueError(f"capacity must be >= 1, got {new_capacity}")
        old_capacity = self._capacity
        self._capacity = new_capacity
        if new_capacity > old_capacity:
            # Only release tokens if we're actually below the new capacity.
            # If held >= new_capacity, we're still over — let release()
            # absorb tokens until held drains to the new level.
            free_slots = new_capacity - self._held
            if free_slots > 0:
                # Release up to the number of new slots (not all of delta,
                # which could overshoot when recovering from over-capacity).
                to_release = min(new_capacity - old_capacity, free_slots)
                for _ in range(to_release):
                    self._sem.release()
        elif new_capacity < old_capacity:
            # Consume available (unheld) tokens so future acquires block.
            # Held tokens drain naturally via release() absorption.
            consumable = min(old_capacity - new_capacity, self._sem._value)  # noqa: SLF001
            for _ in range(consumable):
                self._sem._value -= 1  # noqa: SLF001

    async def acquire(self) -> None:
        await self._sem.acquire()
        self._held += 1

    def release(self) -> None:
        if self._held <= 0:
            raise RuntimeError("release() called more times than acquire()")
        self._held -= 1
        if self._held < self._capacity:
            # Token is recycled — unblocks a waiter or becomes available.
            self._sem.release()
        # else: token is absorbed — we're still over capacity, so don't
        # recycle it. This is how set_capacity() reductions actually take
        # effect when all tokens were held at the time of the reduction.

    async def __aenter__(self) -> Self:
        await self.acquire()
        return self

    async def __aexit__(self, *_: object) -> None:
        self.release()
