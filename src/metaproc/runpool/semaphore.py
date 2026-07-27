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
    adjusts the maximum concurrency. A change event wakes blocked acquirers
    without depending on private asyncio implementation attributes.
    """

    def __init__(self, initial: int = 1) -> None:
        if initial < 1:
            raise ValueError(f"initial capacity must be >= 1, got {initial}")
        self._capacity = initial
        self._held = 0
        self._changed = asyncio.Event()
        self._changed.set()

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
        self._capacity = new_capacity
        if self._held < self._capacity:
            self._changed.set()
        else:
            self._changed.clear()

    async def acquire(self) -> None:
        while self._held >= self._capacity:
            self._changed.clear()
            if self._held < self._capacity:
                break
            await self._changed.wait()
        self._held += 1
        if self._held >= self._capacity:
            self._changed.clear()

    def release(self) -> None:
        if self._held <= 0:
            raise RuntimeError("release() called more times than acquire()")
        self._held -= 1
        if self._held < self._capacity:
            self._changed.set()

    async def __aenter__(self) -> Self:
        await self.acquire()
        return self

    async def __aexit__(self, *_: object) -> None:
        self.release()
