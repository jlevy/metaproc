"""Tests for batch_backend.{infer,resolve,build}_compute_resource."""

from __future__ import annotations

import pytest

from metaproc.cloud.gcp.batch_backend import (
    HOST_CPU_RESERVE_MILLI,
    HOST_MEMORY_RESERVE_MIB,
    infer_compute_resource,
    resolve_compute_resource,
)


# Reference shapes from GCE published machine types.
@pytest.mark.parametrize(
    ("machine_type", "expected_cpu_milli", "expected_mem_mib"),
    [
        # e2-standard-4: 4 vCPU × 4 GiB/vCPU = 16 GiB total → 16384 MiB
        (
            "e2-standard-4",
            4 * 1000 - HOST_CPU_RESERVE_MILLI,
            4 * 4 * 1024 - HOST_MEMORY_RESERVE_MIB,
        ),
        # n2-highmem-8: 8 vCPU × 8 GiB/vCPU = 64 GiB total → 65536 MiB
        ("n2-highmem-8", 8 * 1000 - HOST_CPU_RESERVE_MILLI, 8 * 8 * 1024 - HOST_MEMORY_RESERVE_MIB),
        # n2-highmem-16
        (
            "n2-highmem-16",
            16 * 1000 - HOST_CPU_RESERVE_MILLI,
            16 * 8 * 1024 - HOST_MEMORY_RESERVE_MIB,
        ),
        # c3-standard-4
        (
            "c3-standard-4",
            4 * 1000 - HOST_CPU_RESERVE_MILLI,
            4 * 4 * 1024 - HOST_MEMORY_RESERVE_MIB,
        ),
        # n2-highcpu-8: 8 vCPU × 1 GiB/vCPU
        ("n2-highcpu-8", 8 * 1000 - HOST_CPU_RESERVE_MILLI, 8 * 1024 - HOST_MEMORY_RESERVE_MIB),
        # case insensitive
        ("N2-HIGHMEM-8", 8 * 1000 - HOST_CPU_RESERVE_MILLI, 8 * 8 * 1024 - HOST_MEMORY_RESERVE_MIB),
    ],
)
def test_infer_known_shapes(
    machine_type: str, expected_cpu_milli: int, expected_mem_mib: int
) -> None:
    cpu_milli, mem_mib = infer_compute_resource(machine_type)
    assert cpu_milli == expected_cpu_milli
    assert mem_mib == expected_mem_mib


def test_infer_custom_format() -> None:
    # custom-CPU-MEM where MEM is in MiB
    cpu_milli, mem_mib = infer_compute_resource("custom-6-32768")
    assert cpu_milli == 6 * 1000 - HOST_CPU_RESERVE_MILLI
    assert mem_mib == 32768 - HOST_MEMORY_RESERVE_MIB


@pytest.mark.parametrize(
    "bad",
    [
        "",  # empty
        "bogus",  # unparsable
        "n2-fake-8",  # unknown class
        "z9-standard-4",  # unsupported family
        "n2-standard-0",  # zero vcpus
        "n2-standard",  # missing N
    ],
)
def test_infer_rejects_invalid(bad: str) -> None:
    with pytest.raises(ValueError):
        infer_compute_resource(bad)


def test_resolve_uses_inferred_when_no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METAPROC_GCP_TASK_CPU_MILLI", raising=False)
    monkeypatch.delenv("METAPROC_GCP_TASK_MEMORY_MIB", raising=False)
    cpu_milli, mem_mib = resolve_compute_resource("n2-highmem-8")
    assert cpu_milli == 8 * 1000 - HOST_CPU_RESERVE_MILLI
    assert mem_mib == 8 * 8 * 1024 - HOST_MEMORY_RESERVE_MIB


def test_resolve_honors_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAPROC_GCP_TASK_CPU_MILLI", "3000")
    monkeypatch.setenv("METAPROC_GCP_TASK_MEMORY_MIB", "8000")
    cpu_milli, mem_mib = resolve_compute_resource("n2-highmem-8")
    assert cpu_milli == 3000
    assert mem_mib == 8000


def test_resolve_falls_back_when_machine_type_unparseable(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("METAPROC_GCP_TASK_CPU_MILLI", raising=False)
    monkeypatch.delenv("METAPROC_GCP_TASK_MEMORY_MIB", raising=False)
    with caplog.at_level("WARNING"):
        cpu_milli, mem_mib = resolve_compute_resource("totally-bogus-type")
    # Falls back to legacy Batch default but warns loudly so the failure is visible.
    assert cpu_milli == 2000
    assert mem_mib == 2000
    assert any("Could not infer compute_resource" in rec.message for rec in caplog.records)


def test_resolve_partial_override_keeps_inferred_axis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METAPROC_GCP_TASK_CPU_MILLI", raising=False)
    monkeypatch.setenv("METAPROC_GCP_TASK_MEMORY_MIB", "10000")
    cpu_milli, mem_mib = resolve_compute_resource("e2-standard-4")
    assert cpu_milli == 4 * 1000 - HOST_CPU_RESERVE_MILLI
    assert mem_mib == 10000
