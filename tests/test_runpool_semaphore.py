"""Tests for AdaptiveSemaphore."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from metaproc.runpool.semaphore import AdaptiveSemaphore


class TestAdaptiveSemaphore:
    def test_initial_capacity(self):
        sem = AdaptiveSemaphore(5)
        assert sem.capacity == 5
        assert sem.held == 0
        assert sem.available == 5

    def test_invalid_initial_capacity(self):
        with pytest.raises(ValueError, match="must be >= 1"):
            AdaptiveSemaphore(0)

    def test_acquire_release(self):
        async def _run():
            sem = AdaptiveSemaphore(3)
            await sem.acquire()
            assert sem.held == 1
            assert sem.available == 2
            await sem.acquire()
            assert sem.held == 2
            sem.release()
            assert sem.held == 1
            sem.release()
            assert sem.held == 0

        asyncio.run(_run())

    def test_release_without_acquire_raises(self):
        sem = AdaptiveSemaphore(2)
        with pytest.raises(RuntimeError, match="release.*more times"):
            sem.release()

    def test_increase_capacity_unblocks_waiters(self):
        async def _run():
            sem = AdaptiveSemaphore(1)
            await sem.acquire()  # Take the only slot.

            acquired = asyncio.Event()

            async def waiter():
                await sem.acquire()
                acquired.set()

            task = asyncio.create_task(waiter())
            await asyncio.sleep(0.05)
            assert not acquired.is_set()

            sem.set_capacity(2)  # Release an extra token.
            await asyncio.sleep(0.05)
            assert acquired.is_set()

            sem.release()
            sem.release()
            task.cancel()

        asyncio.run(_run())

    def test_decrease_capacity_blocks_future_acquires(self):
        async def _run():
            sem = AdaptiveSemaphore(3)
            # Acquire 1 of 3.
            await sem.acquire()
            assert sem.held == 1

            # Reduce to 1 — since 1 is held, future acquires should block.
            sem.set_capacity(1)
            assert sem.capacity == 1

            acquired = asyncio.Event()

            async def waiter():
                await sem.acquire()
                acquired.set()

            task = asyncio.create_task(waiter())
            await asyncio.sleep(0.05)
            # The waiter should be blocked because held (1) == capacity (1).
            assert not acquired.is_set()

            sem.release()  # Free the slot.
            await asyncio.sleep(0.05)
            assert acquired.is_set()
            sem.release()
            task.cancel()

        asyncio.run(_run())

    def test_set_capacity_below_one_raises(self):
        sem = AdaptiveSemaphore(3)
        with pytest.raises(ValueError, match="must be >= 1"):
            sem.set_capacity(0)

    def test_context_manager(self):
        async def _run():
            sem = AdaptiveSemaphore(2)
            async with sem:
                assert sem.held == 1
            assert sem.held == 0

        asyncio.run(_run())

    def test_concurrent_acquires_respect_capacity(self):
        async def _run():
            sem = AdaptiveSemaphore(2)
            max_concurrent = 0
            current = 0

            async def worker():
                nonlocal max_concurrent, current
                async with sem:
                    current += 1
                    max_concurrent = max(max_concurrent, current)
                    await asyncio.sleep(0.02)
                    current -= 1

            await asyncio.gather(*[worker() for _ in range(6)])
            assert max_concurrent == 2

        asyncio.run(_run())

    # ── Over-capacity drain tests ──────────────────────────────────

    def test_release_absorbs_tokens_when_over_capacity(self):
        """Bug fix: release() must not recycle tokens when held > capacity.

        Previously, release() always called _sem.release(), so reducing
        capacity from 50 to 1 while 50 were held had no effect — each
        release immediately fed a new acquire, keeping held at ~50.
        """

        async def _run():
            sem = AdaptiveSemaphore(5)
            # Acquire all 5.
            for _ in range(5):
                await sem.acquire()
            assert sem.held == 5

            # Reduce capacity to 2 while all 5 are held.
            sem.set_capacity(2)
            assert sem.capacity == 2
            assert sem.held == 5

            # Release 1 — should be absorbed, not recycled.
            sem.release()
            assert sem.held == 4
            assert sem.available == -2  # still over capacity

            # A new acquire should NOT succeed immediately.
            acquired = asyncio.Event()

            async def waiter():
                await sem.acquire()
                acquired.set()

            task = asyncio.create_task(waiter())
            await asyncio.sleep(0.05)
            assert not acquired.is_set(), "Token was recycled instead of absorbed"

            # Release 2 more — still over (held=2, capacity=2), absorbed.
            sem.release()
            sem.release()
            assert sem.held == 2
            await asyncio.sleep(0.05)
            assert not acquired.is_set(), "Still at capacity, waiter should block"

            # Release 1 more — now held=1 < capacity=2, token recycled.
            sem.release()
            assert sem.held == 1
            await asyncio.sleep(0.05)
            assert acquired.is_set(), "Below capacity, waiter should have acquired"

            # Clean up.
            sem.release()
            sem.release()
            task.cancel()

        asyncio.run(_run())

    def test_over_capacity_drain_blocks_new_launches(self):
        """Simulate the pool scenario: 10 workers, capacity reduced to 3.

        As workers finish, no new ones should start until held drops to 3.
        """

        async def _run():
            sem = AdaptiveSemaphore(10)
            # Simulate 10 running workers.
            for _ in range(10):
                await sem.acquire()
            assert sem.held == 10

            sem.set_capacity(3)

            # Track how many new acquires succeed.
            new_acquires = 0

            async def try_acquire():
                nonlocal new_acquires
                await sem.acquire()
                new_acquires += 1

            # Start 5 waiters.
            tasks = [asyncio.create_task(try_acquire()) for _ in range(5)]
            await asyncio.sleep(0.05)
            assert new_acquires == 0, "No new acquires while over capacity"

            # Release 7 workers (held: 10 → 3). All absorbed, none recycled.
            for _ in range(7):
                sem.release()
            assert sem.held == 3
            await asyncio.sleep(0.05)
            assert new_acquires == 0, "At capacity, still no new acquires"

            # Release 1 more (held: 3 → 2 < capacity 3). Token recycled.
            sem.release()
            await asyncio.sleep(0.05)
            assert new_acquires == 1, "Below capacity, one waiter should proceed"

            # Release the remaining held tokens to unblock more waiters.
            sem.release()
            await asyncio.sleep(0.05)
            assert new_acquires == 2

            # Clean up.
            for t in tasks:
                t.cancel()
            for t in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await t

        asyncio.run(_run())

    def test_increase_capacity_no_overshoot_when_over_capacity(self):
        """Bug fix: increasing capacity when over-capacity must not release
        tokens that would push held even higher.

        Scenario: capacity=1, held=10. set_capacity(3) should NOT release
        2 tokens (which would let 2 new acquires through, making held=12).
        """

        async def _run():
            sem = AdaptiveSemaphore(10)
            for _ in range(10):
                await sem.acquire()

            sem.set_capacity(1)

            # Now increase from 1 to 3. held=10, still way over.
            sem.set_capacity(3)
            assert sem.capacity == 3
            assert sem.held == 10

            # No new acquire should succeed — we're at 10, capacity is 3.
            acquired = asyncio.Event()

            async def waiter():
                await sem.acquire()
                acquired.set()

            task = asyncio.create_task(waiter())
            await asyncio.sleep(0.05)
            assert not acquired.is_set(), (
                "Capacity increase while over-capacity should not release tokens"
            )

            task.cancel()
            # Clean up.
            for _ in range(10):
                sem.release()

        asyncio.run(_run())

    def test_capacity_decrease_then_increase_round_trip(self):
        """Verify correct behavior through a full decrease-then-increase cycle."""

        async def _run():
            sem = AdaptiveSemaphore(5)
            for _ in range(5):
                await sem.acquire()

            # Decrease to 2.
            sem.set_capacity(2)
            assert sem.held == 5
            assert sem.capacity == 2

            # Drain 3 (absorbed).
            for _ in range(3):
                sem.release()
            assert sem.held == 2

            # Increase back to 5 — should release 3 tokens.
            sem.set_capacity(5)
            assert sem.capacity == 5
            assert sem.available == 3

            # 3 new acquires should succeed immediately.
            for _ in range(3):
                await asyncio.wait_for(sem.acquire(), timeout=0.1)
            assert sem.held == 5

            for _ in range(5):
                sem.release()

        asyncio.run(_run())
