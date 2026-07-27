"""Tests for `metaproc.cloud.gcp.billing` (B10, spec §6.5 / OQ3)."""

from __future__ import annotations

import pytest

from metaproc.cloud.gcp.billing import (
    BillableSpan,
    billable_for_span,
    sum_billable,
)


def test_billable_for_span_derives_three_metrics_from_machine_type() -> None:
    span = billable_for_span(elapsed_s=3600.0, machine_type="n2-standard-4")
    # n2-standard-4 = 4 vCPU, 16 GiB RAM, minus host reserves
    # (HOST_CPU_RESERVE_MILLI=500, HOST_MEMORY_RESERVE_MIB=1024).
    # claimed cpu_milli = 4000 - 500 = 3500
    # claimed memory_mib = 16384 - 1024 = 15360
    assert span.vm_hours == pytest.approx(1.0)
    assert span.vcpu_hours == pytest.approx(3.5)
    assert span.memory_gib_hours == pytest.approx(15360.0 / 1024.0)


def test_billable_for_span_accepts_explicit_overrides() -> None:
    span = billable_for_span(
        elapsed_s=1800.0,  # half hour
        machine_type=None,
        cpu_milli=2000,
        memory_mib=4096,
    )
    assert span.vm_hours == pytest.approx(0.5)
    assert span.vcpu_hours == pytest.approx(1.0)
    assert span.memory_gib_hours == pytest.approx(2.0)


def test_billable_for_span_zero_elapsed_yields_zero_metrics() -> None:
    span = billable_for_span(elapsed_s=0.0, machine_type="n2-standard-4")
    assert span.vm_hours == 0.0
    assert span.vcpu_hours == 0.0
    assert span.memory_gib_hours == 0.0


def test_billable_for_span_rejects_negative_elapsed() -> None:
    with pytest.raises(ValueError):
        billable_for_span(elapsed_s=-1.0, machine_type="n2-standard-4")


def test_billable_for_span_requires_machine_type_when_overrides_missing() -> None:
    with pytest.raises(ValueError):
        billable_for_span(elapsed_s=60.0, machine_type=None)


def test_sum_billable_is_associative() -> None:
    a = BillableSpan(vm_hours=1.0, vcpu_hours=2.0, memory_gib_hours=4.0)
    b = BillableSpan(vm_hours=0.5, vcpu_hours=1.0, memory_gib_hours=2.0)
    c = BillableSpan(vm_hours=0.25, vcpu_hours=0.5, memory_gib_hours=1.0)

    total = sum_billable([a, b, c])
    assert total.vm_hours == pytest.approx(1.75)
    assert total.vcpu_hours == pytest.approx(3.5)
    assert total.memory_gib_hours == pytest.approx(7.0)


def test_sum_billable_empty_returns_zero_span() -> None:
    total = sum_billable([])
    assert total == BillableSpan(0.0, 0.0, 0.0)


def test_billable_span_supports_addition() -> None:
    a = BillableSpan(vm_hours=1.0, vcpu_hours=2.0, memory_gib_hours=4.0)
    b = BillableSpan(vm_hours=2.0, vcpu_hours=4.0, memory_gib_hours=8.0)
    assert (a + b) == BillableSpan(vm_hours=3.0, vcpu_hours=6.0, memory_gib_hours=12.0)
