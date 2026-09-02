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
owns the system design, and the
[Safeproc local-incubation plan](../specs/active/plan-2026-09-01-safeproc-local-incubation.md)
owns the standalone package boundary and repository mechanics.

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

`memory_pressure` labels `kern.memorystatus_level` as
`System-wide memory free percentage`. XNU’s
[`AVAILABLE_NON_COMPRESSED_MEMORY`](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/vm/vm_page.h)
includes active pages, so other processes’ hot working sets may still count toward the
reported level. A rapid allocation wave can therefore leave the percentage looking
healthy until compression and reclaim are already active.

The relevant XNU definitions are:

```c
#define AVAILABLE_NON_COMPRESSED_MEMORY \
    (vm_page_active_count + vm_page_inactive_count + \
     vm_page_free_count + vm_page_speculative_count)

#define VM_CHECK_MEMORYSTATUS \
    memorystatus_update_available_page_count(AVAILABLE_NON_COMPRESSED_MEMORY)
```

The first term is the trap: active pages are in-use working sets, not headroom for a new
allocation wave. On two recorded crash days the gauge still read 87 to 90 percent while
the host failed, because new allocations initially remained active pages.
In a same-moment cross-check on the measurement host, the level reported 49 percent of
34.36 GB, or 16.84 GB, while VM counters showed only 0.49 GB free, 8.47 GB
free-plus-inactive-plus-purgeable, and 11.58 GB held by the compressor.
Total memory minus compressed and wired memory reconciled to the reported percentage:
34.36 GB minus 11.58 GB compressed and about 5.9 GB wired is about 16.9 GB. The
arithmetic was consistent and the budget interpretation was wrong.

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

More precisely, the ledger adds internal anonymous memory after alternate-accounting
adjustments, internal compressed memory after the equivalent adjustments, I/O mappings,
nonvolatile purgeable and compressed-purgeable pages, and page tables.
The binary API is `task_info(task, TASK_VM_INFO, ...)`, whose `task_vm_info` result
contains `phys_footprint`; `footprint(1)` exposes the same concept at the command line.
One live process measured 58 MB RSS and 91 MB physical footprint while the host
compressor held 11.58 GB, demonstrating the compressed-memory difference directly.

This makes RSS wrong in both directions during fan-out.
`phys_footprint`, summed over a verified tree, is the preferred estimate for attribution
and victim sizing. RSS remains a fallback and terminal calibration field, not the
authoritative macOS crisis metric.

Host counters come from `host_statistics64(HOST_VM_INFO64)` and `vm_statistics64`, the
same family printed by `vm_stat(1)`. Its `free_count` already includes speculative
pages; `compressor_page_count` describes the compressed pager; and `swapins` and
`swapouts` are lifetime totals.
A degradation detector must use swap deltas, not the absolute counters.

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

The calculation begins with free pages minus zone reserves, then adds file-cache and
reclaimable-kernel estimates after subtracting up to half of each component, bounded by
low watermarks. This is why `free + cached` is not an equivalent user-space formula:
cache can include unreclaimable tmpfs or shared memory and omit reclaimable slab.

Use `MemAvailable` for host admission.
In a delegated cgroup v2 environment, also use `memory.current`, `memory.events`, and
`memory.pressure` for the narrower containment scope.

### PSS Represents Shared Pages Proportionally

Linux [`smaps_rollup`](https://www.kernel.org/doc/html/latest/filesystems/proc.html)
reports proportional set size (PSS), dividing each shared page among the processes that
map it. This is the preferred process-tree cost when a cgroup total is unavailable.
RSS decomposes into anonymous, file-backed, and shared-memory residents, while swapped
or compressed pages may remain outside the process RSS figure.

For example, a process with 1,000 private pages and 1,000 pages shared with one other
process has a PSS of 1,500 pages.
`/proc/<pid>/smaps_rollup` also exposes `Pss_Anon`, `Pss_File`, and `Pss_Shmem`; the
implementation lives in `fs/proc/task_mmu.c`. `VmRSS` is the sum of anonymous, file, and
shared-memory residents, while `VmSwap` records process pages swapped out of RSS. Under
zram or zswap, process RSS excludes swapped pages even though their compressed
representation still consumes physical RAM elsewhere, which is analogous to the macOS
compressor problem.

### PSI Measures Lost Time

[Pressure Stall Information](https://docs.kernel.org/accounting/psi.html), exposed
host-wide at `/proc/pressure/memory` and per cgroup at `memory.pressure`, reports the
share of time at least one task (`some`) or all non-idle tasks (`full`) are stalled on a
resource.
This distinguishes a host that is full but reclaiming efficiently from one that
is losing useful work to memory contention.
PSI is a degradation and load-shedding signal; it is not a replacement for byte
reservations.

Burst shape matters even when total work is unchanged.
In one recorded resume event, available pages fell from 342,417 to 276,077 in two
seconds because every in-flight unit relaunched at once.
A paced restart would perform the same work with a different stall and compression
curve.

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

## Metaproc Implementation Snapshot

The 2026-09-01 code audit found useful foundations and several gaps.
This table records the state at that commit; implementation must recheck it rather than
treating an open plan as evidence that the code is unchanged.

| Concern | Current behavior | Required change |
| --- | --- | --- |
| macOS host budget | `memory_pressure.py` uses free, inactive, and purgeable `vm_stat` pages and reads memorystatus only as an alarm | Preserve the semantics while replacing subprocess sampling on the crisis path with native calls |
| Linux host budget | The provider uses `MemAvailable` and optionally refines degradation with host PSI | Preserve the byte budget; model PSI as stall evidence rather than an inverted capacity percentage |
| Cross-process admission | `HostAdmissionGate` serializes a count-only slot lease across independent Metaproc parents | Introduce versioned resource claims and host-wide launch pacing without rewriting live v1 leases |
| Direct scalar launch | A timeout or slot-directory `OSError` logs a warning and launches without a claim | Fail closed for ordinary operation; retain only an explicit unsafe development override |
| Process profile | Initial concurrency uses one `estimated_process_rss_bytes` value | Represent startup peak, startup duration, steady cost, state regime, and launch spacing |
| Failure domain | Scheduling, telemetry, and response remain inside Metaproc processes | Add an elected broker or sentinel that can embargo launches if one RunPool stalls |

The current macOS measurement uses helper commands for `vm_stat` and `sysctl`. That is
acceptable for ordinary pool telemetry but not a demonstrated crisis-path guarantee: a
memory-starved host may be unable to fork the very helper required to decide whether it
is safe. The independent sentinel should use native host and task APIs, record actual
cadence and lag, and treat a missing or stale sample as a capability failure rather than
inventing safe headroom.

## Reusable Guard Evidence

A downstream macOS guard supplied operating evidence that is useful beyond its original
script:

- one 15-unit fan-out drove a 34 GB host to its highest recorded pressure state and a
  `vm-compressor-space-shortage` event while individual agent trees reached 3.6-5.3 GB;
- a strictly passive complete-tree observer ran for 4,896 one-second samples beside a
  healthy fan-out, survived a sleep and wake cycle, and took no action;
- a synthetic tree established that a small launcher PID may not own the memory and that
  cleanup has to cover descendants rather than the root alone;
- incident use later recorded 352 worker sheds across 22 workload legs when the guard
  was configured as a throughput governor, consuming completed model work and proving
  that routine intervention belongs in admission and pacing instead;
- during one compressed-memory episode, tree RSS fell from 6.17 GB to 1.24 GB while host
  compressed memory rose from 17.6 GB to 26.1 GB, confirming that RSS can rank emergency
  victims in the wrong order;
- observation and intervention simulation must remain different modes: a command that
  pauses producers, even if it suppresses termination, is not a passive profiler.

In the source guard, `--observe-only` was the passive mode and `--dry-run` still
simulated intervention.
The names are not a portable API requirement, but the authority distinction is.

The script’s zero-dependency, plain-argument-parser shape is worth retaining in the
standalone safety path.
Its macOS-only gauges, helper-process sampling, and incident-calibrated policy are
evidence inputs rather than a library API to preserve.
The reusable design should port the contracts and replay corpus to macOS and Linux, then
validate one shared policy over normalized evidence.

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

The distinction changed an actual incident diagnosis.
Among 189 failed authoring attempts, 69 aligned with guard kills and 45 contained a
successful final transcript result and declared output followed by a nonzero client
exit.
The same five work items that failed under resource constraint later completed five
for five with better headroom and no sheds.
Resource preemption, exit-fidelity defects, and invalid model output require different
remedies and retry policy.

## Primary Evidence

| Claim | Source | Evidence status |
| --- | --- | --- |
| macOS available-noncompressed memory includes active pages | [XNU VM page accounting](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/vm/vm_page.h) | Source definition checked |
| `kern.memorystatus_level` is the percentage exposed by `memory_pressure` | [XNU memorystatus implementation](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_memorystatus.c) and same-moment local comparison | Source and measurement checked |
| Physical footprint includes compressed internal memory and page-table cost | [XNU task ledgers](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/kern/task.c) and SDK `mach/task_info.h` | Source and local API checked |
| VM free, compressor, and lifetime swap counters | SDK `mach/vm_statistics.h` and `vm_stat(1)` | Source and command output checked |
| `MemAvailable` subtracts reserves and estimates reclaimable cache and slab | [Linux implementation](https://github.com/torvalds/linux/blob/master/mm/show_mem.c) and [introducing commit](https://github.com/torvalds/linux/commit/34e431b0ae398fc54ea69ff85ec700722c9da773) | Source and rationale checked |
| PSS divides shared pages and RSS excludes swapped pages | [Linux proc documentation](https://www.kernel.org/doc/html/latest/filesystems/proc.html) | Kernel documentation checked |
| PSI reports time lost to resource stalls | [Linux PSI documentation](https://docs.kernel.org/accounting/psi.html) | Kernel documentation checked |
| Gemini startup demand changes with project-state access | [Gemini project-state research](research-2026-09-01-gemini-cli-project-state-memory.md) | Controlled cause established |
| Four-client startup scale and profile requirements | [Agent CLI startup research](research-2026-09-01-agent-cli-memory-usage.md) | One-shot comparison complete; distributions open |

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
9. Keep the first Safeproc release narrower than a pool: brokerless passive monitoring,
   owned launch, a small broker or sentinel, and deterministic replay over one safety
   core.

## References

- [XNU VM page accounting](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/vm/vm_page.h)
- [XNU task ledgers](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/kern/task.c)
- [XNU memorystatus notifications](https://github.com/apple-oss-distributions/xnu/blob/main/doc/vm/memorystatus_notify.md)
- [Linux `MemAvailable` implementation](https://github.com/torvalds/linux/blob/master/mm/show_mem.c)
- [Linux proc filesystem memory fields](https://www.kernel.org/doc/html/latest/filesystems/proc.html)
- [Linux Pressure Stall Information](https://docs.kernel.org/accounting/psi.html)
- [Linux cgroup v2 memory controller](https://docs.kernel.org/admin-guide/cgroup-v2.html#memory)
- [Agent CLI Startup Memory](research-2026-09-01-agent-cli-memory-usage.md)
- [Gemini CLI Project-State Startup Memory](research-2026-09-01-gemini-cli-project-state-memory.md)
- [RunPool host-safety plan](../specs/active/plan-2026-09-01-runpool-host-safety.md)
- [Safeproc local-incubation plan](../specs/active/plan-2026-09-01-safeproc-local-incubation.md)
- [Standalone macOS memory guard](https://gist.github.com/jlevy/5b43e0d44166b9c7fe8157ee938cb0d5)

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
