"""Host admission on the scalar (non-fan-out) launch path.

The behaviour under test is that a direct adapter launch takes a host slot, and the
behaviour under test just as much is that it degrades to launching anyway when the gate
is unreachable. A step that ran before admission existed must not start failing because
a shared slot directory is unwritable.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from metaproc.runpool.host_admission import (
    HostAdmissionGate,
    list_host_admission_slots,
)
from metaproc.runpool.scalar_admission import admitted_launch


def _held(root: Path, namespace: str) -> int:
    """Count slots still occupied by a live owner."""
    slots = list_host_admission_slots(root_dir=root, namespace=namespace)
    return len([s for s in slots if s.status == "alive"])


class TestSlotIsHeld:
    def test_a_launch_occupies_a_host_slot_for_its_duration(self, tmp_path: Path) -> None:
        async def run() -> None:
            async with admitted_launch(
                enabled=True, limit=2, label="run/step", pool_id="p1", root_dir=tmp_path
            ) as lease:
                assert lease is not None
                assert _held(tmp_path, lease.namespace) == 1

        asyncio.run(run())

    def test_the_slot_is_released_afterwards(self, tmp_path: Path) -> None:
        async def run() -> str:
            async with admitted_launch(
                enabled=True, limit=2, label="run/step", pool_id="p1", root_dir=tmp_path
            ) as lease:
                assert lease is not None
                return lease.namespace

        namespace = asyncio.run(run())
        assert _held(tmp_path, namespace) == 0

    def test_the_slot_is_released_even_when_the_launch_raises(self, tmp_path: Path) -> None:
        """A crashing adapter must not leak capacity."""
        captured: dict[str, str] = {}

        async def run() -> None:
            async with admitted_launch(
                enabled=True, limit=2, label="run/step", pool_id="p1", root_dir=tmp_path
            ) as lease:
                assert lease is not None
                captured["namespace"] = lease.namespace
                raise RuntimeError("adapter died")

        with pytest.raises(RuntimeError):
            asyncio.run(run())

        assert _held(tmp_path, captured["namespace"]) == 0


class TestMutualExclusion:
    def test_concurrent_scalar_launches_share_one_host_limit(self, tmp_path: Path) -> None:
        """Two independent launches at limit=1 serialise.

        This is the property the recorded host crashes needed and did not have: a resume
        that re-dispatched every in-flight item arrived as one simultaneous wave.
        """
        order: list[str] = []

        async def launch(name: str) -> None:
            async with admitted_launch(
                enabled=True, limit=1, label=name, pool_id=name, root_dir=tmp_path
            ) as lease:
                assert lease is not None
                order.append(f"enter:{name}")
                await asyncio.sleep(0.05)
                order.append(f"exit:{name}")

        async def run() -> None:
            await asyncio.gather(launch("a"), launch("b"))

        asyncio.run(run())

        # Whoever went first, the other cannot have entered before that one left.
        assert order[0].startswith("enter:")
        assert order[1].startswith("exit:")
        assert order[1].split(":")[1] == order[0].split(":")[1]

    def test_launches_run_concurrently_when_the_limit_allows(self, tmp_path: Path) -> None:
        overlapped = False

        async def launch(name: str, peer: asyncio.Event, mine: asyncio.Event) -> None:
            nonlocal overlapped
            async with admitted_launch(
                enabled=True, limit=2, label=name, pool_id=name, root_dir=tmp_path
            ):
                mine.set()
                try:
                    await asyncio.wait_for(peer.wait(), timeout=2.0)
                    overlapped = True
                except TimeoutError:
                    pass

        async def run() -> None:
            a, b = asyncio.Event(), asyncio.Event()
            await asyncio.gather(launch("a", b, a), launch("b", a, b))

        asyncio.run(run())

        assert overlapped


class TestDegradesRatherThanFails:
    def test_disabled_admission_yields_no_lease_and_still_runs(self, tmp_path: Path) -> None:
        ran = False

        async def run() -> None:
            nonlocal ran
            async with admitted_launch(
                enabled=False, limit=4, label="run/step", pool_id="p1", root_dir=tmp_path
            ) as lease:
                ran = True
                assert lease is None

        asyncio.run(run())
        assert ran

    def test_a_nonpositive_limit_yields_no_lease_and_still_runs(self, tmp_path: Path) -> None:
        async def run() -> None:
            async with admitted_launch(
                enabled=True, limit=0, label="run/step", pool_id="p1", root_dir=tmp_path
            ) as lease:
                assert lease is None

        asyncio.run(run())

    def test_an_unreachable_gate_does_not_fail_the_launch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Adding admission must not add a new outage mode."""

        async def boom(*_args: object, **_kwargs: object) -> None:
            raise OSError("slot directory is not writable")

        monkeypatch.setattr(HostAdmissionGate, "acquire", boom)
        ran = False

        async def run() -> None:
            nonlocal ran
            async with admitted_launch(
                enabled=True, limit=2, label="run/step", pool_id="p1", root_dir=tmp_path
            ) as lease:
                ran = True
                assert lease is None

        asyncio.run(run())
        assert ran

    def test_a_timed_out_acquire_does_not_fail_the_launch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def slow(*_args: object, **_kwargs: object) -> None:
            raise TimeoutError

        monkeypatch.setattr(HostAdmissionGate, "acquire", slow)

        async def run() -> None:
            async with admitted_launch(
                enabled=True, limit=2, label="run/step", pool_id="p1", root_dir=tmp_path
            ) as lease:
                assert lease is None

        asyncio.run(run())
