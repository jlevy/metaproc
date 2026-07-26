"""Approximate GCP Batch billable hours for resource attribution.

The resource report cannot reconcile against actual GCP invoices, so this
module approximates billable hours from two pieces of evidence the run already
records:

1. **Requested resources** — vCPU + memory derived from the VM's machine
   type via :func:`metaproc.cloud.gcp.batch_backend.infer_compute_resource`.
2. **Worker runtime spans** — the time between a runpool ``process_start``
   and matching ``process_exit`` event in a runpool `events.jsonl` stream.

Batch pricing combines VM runtime, vCPU, and memory, so no single number
captures cost truthfully. Local runs leave the billable-hour metrics unset.
"""

from __future__ import annotations

from dataclasses import dataclass

from metaproc.cloud.gcp.batch_backend import infer_compute_resource

SECONDS_PER_HOUR = 3600.0
MIB_PER_GIB = 1024.0


@dataclass(frozen=True)
class BillableSpan:
    """One worker's billable contribution.

    ``vm_hours`` is the wall-clock VM uptime; ``vcpu_hours`` and
    ``memory_gib_hours`` are derived by multiplying ``vm_hours`` by the
    container's claimed vCPU and memory. Sum across spans to roll up to
    a step / process / run.
    """

    vm_hours: float
    vcpu_hours: float
    memory_gib_hours: float

    def __add__(self, other: BillableSpan) -> BillableSpan:
        return BillableSpan(
            vm_hours=self.vm_hours + other.vm_hours,
            vcpu_hours=self.vcpu_hours + other.vcpu_hours,
            memory_gib_hours=self.memory_gib_hours + other.memory_gib_hours,
        )


def billable_for_span(
    *,
    elapsed_s: float,
    machine_type: str | None,
    cpu_milli: int | None = None,
    memory_mib: int | None = None,
) -> BillableSpan:
    """Compute the billable hours for one worker span.

    ``elapsed_s`` is the wall-clock time the worker was alive (typically
    derived from runpool ``process_exit.elapsed_s``).

    Provide ``machine_type`` to derive vCPU + memory automatically, or
    pass ``cpu_milli`` / ``memory_mib`` explicitly when the operator
    has overridden via ``METAPROC_GCP_TASK_CPU_MILLI`` /
    ``METAPROC_GCP_TASK_MEMORY_MIB`` (matching the override behaviour
    in :mod:`metaproc.cloud.gcp.batch_backend`).
    """
    if elapsed_s < 0:
        raise ValueError(f"elapsed_s must be non-negative, got {elapsed_s!r}")

    if cpu_milli is None or memory_mib is None:
        if not machine_type:
            raise ValueError("Need machine_type when cpu_milli/memory_mib are not provided")
        inferred_cpu, inferred_memory = infer_compute_resource(machine_type)
        cpu_milli = cpu_milli if cpu_milli is not None else inferred_cpu
        memory_mib = memory_mib if memory_mib is not None else inferred_memory

    vm_hours = elapsed_s / SECONDS_PER_HOUR
    vcpu_hours = vm_hours * (cpu_milli / 1000.0)
    memory_gib_hours = vm_hours * (memory_mib / MIB_PER_GIB)

    return BillableSpan(
        vm_hours=vm_hours,
        vcpu_hours=vcpu_hours,
        memory_gib_hours=memory_gib_hours,
    )


def sum_billable(spans: list[BillableSpan]) -> BillableSpan:
    """Aggregate billable spans for a step / process / run."""
    total = BillableSpan(0.0, 0.0, 0.0)
    for span in spans:
        total = total + span
    return total
