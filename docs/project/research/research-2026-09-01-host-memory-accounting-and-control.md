---
title: Host Memory Accounting and Control
description: macOS and Linux memory gauges, process-tree cost attribution, and the distinct roles of admission, launch pacing, and emergency containment.
date: 2026-09-01
status: Complete
---
# Research: Host Memory Accounting and Control

**Date:** 2026-09-01

**Status:** Complete; implementation gaps remain in the active plan

## Overview

Local agent fan-out is constrained by time-varying process-tree memory, not by a fixed
worker count. The operating systems expose measurements with different scopes and
semantics, and using the wrong gauge can authorize a burst while the host is already
degrading.

The durable control model has three separate mechanisms:

- **admission** reserves current host capacity before an executable leaf starts;
- **launch pacing** shapes overlapping startup transients;
- **emergency containment** acts only after measured critical pressure and must remain
  independent of the normal scheduler.

The [agent CLI research](research-2026-09-01-agent-cli-memory-usage.md) supplies
measured startup curves.
The [RunPool host-safety plan](../specs/active/plan-2026-09-01-runpool-host-safety.md)
owns implementation.

## Questions

1. Which host gauge is a usable memory budget on macOS and Linux?
2. Which per-process metric best estimates the memory recovered by terminating work?
3. How should complete process trees, shared pages, and compression be represented?
4. Which signals show degradation rather than merely low unused memory?
5. Which responsibilities belong to admission, pacing, and a final guard?

## Measurement Model

Every observation needs an explicit scope: `root`, `tree`, `cgroup`, or `host`. Evidence
at one scope must not be promoted silently to another.
An owned-process launch can establish authoritative process-group or cgroup membership;
a monitor of an existing process must rediscover and identity-fence a changing tree.

| Platform and purpose | Preferred evidence | Misleading substitute |
| --- | --- | --- |
| macOS host headroom | Free, inactive, and purgeable pages from host VM statistics | Treating `kern.memorystatus_level` as a byte budget |
| macOS process cost | Complete-tree `phys_footprint` | Root PID only or summed RSS |
| macOS degradation | Kernel pressure state, compressor growth, and swap deltas | A single free-memory percentage |
| Linux host headroom | `/proc/meminfo` `MemAvailable` | Free plus cached arithmetic |
| Linux process cost | Cgroup `memory.current` or complete-tree PSS from `smaps_rollup` | Summed RSS |
| Linux degradation | PSI `some` and `full`, plus `memory.events` when cgroup v2 is available | A capacity percentage without stall evidence |

## macOS Findings

### Memorystatus Is an Alarm, Not a Byte Budget

`memory_pressure` reports `kern.memorystatus_level`. XNU’s
[`AVAILABLE_NON_COMPRESSED_MEMORY`](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/vm/vm_page.h)
includes active pages, so other processes’ hot working sets may still count toward the
reported level. A rapid allocation wave can therefore leave the percentage looking
healthy until compression and reclaim are already active.

Use free, inactive, and purgeable pages for admission headroom.
Use the kernel
[memorystatus notification](https://github.com/apple-oss-distributions/xnu/blob/main/doc/vm/memorystatus_notify.md)
state as an alarm and state-machine input, not as `total_memory × percentage`.

### Physical Footprint Is the Process-Cost Metric

XNU’s
[`phys_footprint` ledger](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/kern/task.c)
includes internal compressed memory, I/O mappings, nonvolatile purgeable memory, and
page-table cost. RSS omits compressed pages, while summing RSS across related processes
can count shared file-backed pages repeatedly.

This makes RSS wrong in both directions during fan-out.
`phys_footprint`, summed over a verified tree, is the preferred estimate for attribution
and victim sizing. RSS remains a fallback and terminal calibration field, not the
authoritative macOS crisis metric.

### Compression Can Invert the Apparent Trend

In one pressure episode, the guarded tree’s RSS fell from 6.17 GB to 1.24 GB while host
compressed memory rose from 17.6 GB to 26.1 GB. The apparent process cost improved as
the host became less usable.
A guard that ranks victims by RSS during compression may therefore select the wrong work
or conclude that the guarded tree cannot help.

Sampling must also survive the condition it measures.
The critical path should use native host and task APIs without forking helper commands,
record actual cadence and lag, and distrust stale samples without treating sampling
delay itself as proof of memory pressure.

## Linux Findings

### `MemAvailable` Is the Kernel’s Headroom Estimate

Linux added `MemAvailable` because user-space free-plus-cache formulas did not account
correctly for reserves, unfreeable cache, and reclaimable slab.
The kernel’s
[`si_mem_available()`](https://github.com/torvalds/linux/blob/master/mm/show_mem.c)
subtracts reserves and adds conservative portions of file cache and reclaimable kernel
memory. The rationale is recorded in
[the introducing commit](https://github.com/torvalds/linux/commit/34e431b0ae398fc54ea69ff85ec700722c9da773).

Use `MemAvailable` for host admission.
In a delegated cgroup v2 environment, also use `memory.current`, `memory.events`, and
`memory.pressure` for the narrower containment scope.

### PSS Represents Shared Pages Proportionally

Linux [`smaps_rollup`](https://www.kernel.org/doc/html/latest/filesystems/proc.html)
reports proportional set size (PSS), dividing each shared page among the processes that
map it. This is the preferred process-tree cost when a cgroup total is unavailable.
RSS decomposes into anonymous, file-backed, and shared-memory residents, while swapped
or compressed pages may remain outside the process RSS figure.

### PSI Measures Lost Time

[Pressure Stall Information](https://docs.kernel.org/accounting/psi.html) reports the
share of time at least one task (`some`) or all non-idle tasks (`full`) are stalled on a
resource.
This distinguishes a host that is full but reclaiming efficiently from one that
is losing useful work to memory contention.
PSI is a degradation and load-shedding signal; it is not a replacement for byte
reservations.

## Control Responsibilities

| Mechanism | Decision scope | Safe authority | Failure meaning |
| --- | --- | --- | --- |
| Admission | Host-wide current headroom and outstanding startup reservations | Delay or refuse a new executable leaf | A required gauge or claim failure must fail closed |
| Launch pacing | Host-wide overlap between compatible startup profiles | Delay otherwise admissible starts | Excess spacing costs startup latency, not active work |
| Cooperative pressure response | Host state plus registered claims | Freeze new starts and ask owners to drain | Predictive evidence may embargo launches but not kill work |
| Emergency containment | Sustained measured critical pressure with identity and attribution | Stop producers, shed bounded restartable work, or abort an owned tree | Routine activation indicates defective admission, pacing, or adapter mitigation |

Admission and pacing must operate at the executable-agent boundary.
Spacing a parent job does not bound a parent that starts several agent leaves together.
A startup claim reserves the declared peak before spawn and remains until the startup
window ends or reliable attributable evidence proves that the process settled.

Predictive signals such as reclaimable-memory slope or fast compressor growth may close
admission. They must not authorize destructive action.
Shedding requires sustained measured danger, attribution confidence, and one elected
responder so independent pools do not kill simultaneously.

## Owned Launch and Existing-Process Monitoring

Owned launch can establish admission, isolated group identity, and cleanup authority
before the target executes.
It can signal the owned group and make a strong containment claim.

Existing-process monitoring cannot apply admission retroactively and cannot trust an
inherited process group.
It remains useful for profiling and exceptional protection, but every target selected
for intervention must be revalidated by PID and creation time as part of the currently
observed tree. Observation and journaling should be its default authority.

Both modes should normalize the same host evidence and reach the same pressure state.
They differ in which actions are safe and which guarantees they can state.

## Failure Reconstruction

A memory intervention is not an adapter failure.
The supervisor journal must record the target identity, evidence, action authority,
pressure episode, and signal result so the caller can distinguish workload exit,
supervisor failure, timeout, external signal, and host-pressure preemption.

Agent runtimes may also produce a successful transcript and declared output before a
wrapper exits nonzero.
Metaproc should reconcile those higher-level facts with the generic supervisor result
instead of converting every nonzero exit into the same retry or prompt diagnosis.

## Recommendations

1. Implement one normalized host-sample model with platform-specific evidence and
   explicit metric scope.
2. Use complete-tree physical footprint on macOS and cgroup accounting or PSS on Linux.
3. Admit every executable leaf against current headroom plus outstanding startup
   reservations; missing required evidence fails closed.
4. Pace compatible startups across all local clients, not per pool or parent process.
5. Keep passive existing-process profiling separate from intervention simulation.
6. Keep emergency containment small, independent, identity-fenced, and exceptional.
7. Persist enough evidence to replay pressure decisions and distinguish preemption from
   workload failure.
8. Recalibrate adapter profiles by version, platform, model, and relevant state regime.

## References

- [XNU VM page accounting](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/vm/vm_page.h)
- [XNU task ledgers](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/kern/task.c)
- [XNU memorystatus notifications](https://github.com/apple-oss-distributions/xnu/blob/main/doc/vm/memorystatus_notify.md)
- [Linux `MemAvailable` implementation](https://github.com/torvalds/linux/blob/master/mm/show_mem.c)
- [Linux proc filesystem memory fields](https://www.kernel.org/doc/html/latest/filesystems/proc.html)
- [Linux Pressure Stall Information](https://docs.kernel.org/accounting/psi.html)
- [Linux cgroup v2 memory controller](https://docs.kernel.org/admin-guide/cgroup-v2.html#memory)
- [Agent CLI Startup Memory](research-2026-09-01-agent-cli-memory-usage.md)
- [RunPool host-safety plan](../specs/active/plan-2026-09-01-runpool-host-safety.md)

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
