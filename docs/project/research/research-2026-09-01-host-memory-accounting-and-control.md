---
title: Host Memory Accounting and Control
description: macOS and Linux memory gauges, process-tree cost attribution, and the distinct roles of admission, launch pacing, and emergency containment.
date: 2026-09-01
last_updated: 2026-09-02
status: Complete
---
# Research: Host Memory Accounting and Control

**Date:** 2026-09-01 (last updated 2026-09-02)

**Status:** Complete; implementation gaps remain in the active plan

This record owns the control model: what admission, pacing, and containment may each
decide, and which evidence each may use.
The gauge semantics behind it, with kernel citations and reproduction commands, are
owned by the repository’s
[memory accounting reference](../../memory-accounting-reference.md); the sections below
summarize those facts and link there rather than restating the measurements.

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

PSI is not always present and not always writable.
`/proc/pressure` is absent when the kernel was built without `CONFIG_PSI`, when `psi=0`
was passed at boot, and often inside containers.
Creating a trigger, a write to the file followed by `poll`, required `CAP_SYS_RESOURCE`
before kernel 6.5; since 6.5 unprivileged users may create triggers whose window is a
multiple of two seconds.
The cgroup-local `memory.pressure` file in a process’s own cgroup is readable without
privilege.
A provider therefore reports PSI as one of three capability states rather than
as present or absent.

### `MemAvailable` Inside a Limited Cgroup

`MemAvailable` is a host figure.
Inside a cgroup with `memory.max` set, which is the normal condition for containers,
cloud workers, and GCP Batch tasks, the host figure can exceed what the cgroup may use
by an order of magnitude.
The usable budget is the smaller of host `MemAvailable` and the cgroup’s own headroom,
`memory.max` minus `memory.current`, read from the cgroup the process belongs to.
Metaproc’s `osutils/resource_context.py` already reads these files for diagnostics.

### No Compressor, and a Kernel Safety Net

Linux has no compressor unless zswap or zram is configured, so the compressor-slope
predictor that the macOS guard corpus validated has no Linux analogue.
The predictive signals are `some` stall and `MemAvailable` slope; the measured
degradation signal beside `full` stall is swap-in rate, `pswpin` in `/proc/vmstat`,
because swap used remains high after an episode ends.

Linux also has what macOS lacks: a kernel OOM killer.
It is a safety net that may kill the orchestrator, the broker, or an unrelated process,
and it chooses by `oom_score`. Raising `oom_score_adj` is unprivileged, so a launcher
can mark agent leaves as preferred victims; lowering it below the inherited value
requires `CAP_SYS_RESOURCE`. An OOM kill is visible as an exit by `SIGKILL` together
with an `oom_kill` count in the cgroup’s `memory.events`, and a supervisor should
classify it as host-pressure preemption, not adapter failure.

### Identity and Containment Primitives

`pidfd_open`, kernel 5.3 and later, returns a descriptor that refers to one process
incarnation, cannot be recycled, and becomes readable when the process exits;
`pidfd_send_signal` signals that identity.
For owned children this is strictly stronger than PID plus start time.

`cgroup.kill`, kernel 5.14 and later, terminates every process in a cgroup atomically,
which removes the enumeration race that deepest-first tree walks exist to mitigate.
`clone3` with `CLONE_INTO_CGROUP` places a child in a cgroup at creation but is not
reachable from Python; a launch wrapper instead writes its own PID to `cgroup.procs`
before `exec`. Delegation is the gating condition: on systemd hosts
`systemd-run --user --scope` gives an unprivileged user a delegated cgroup, and without
delegation a supervisor falls back to process groups.

`PR_SET_CHILD_SUBREAPER` makes a process the reparenting target for its orphaned
descendants, so grandchildren whose parent died reparent to the wrapper rather than to
`init` and remain findable by a tree walk.

### Sampling Cost and Clocks

`smaps_rollup` walks page tables to compute PSS; on a multi-gigabyte process one read
costs tens of milliseconds, and a tree is sampled every interval.
Cgroup `memory.current` is constant time.
The macOS guard’s finding that a reading is nearly free, 24 microseconds for host
statistics and 0.2 milliseconds for a 60-process tree, does not carry to Linux PSS, so a
Linux provider needs a sampling-cost budget and an accuracy gate.

`CLOCK_MONOTONIC` stops during system suspend on Linux; `CLOCK_BOOTTIME` does not.
They are the Linux counterparts of the macOS active and continuous clocks, and a
deadline must name which one it uses.

No Linux failure corpus exists yet.
Every Linux threshold in the plans is a design choice awaiting calibration on a
dedicated host.

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

The script’s zero-dependency, plain-argument-parser shape is worth retaining in the
standalone safety path.
Its macOS-only gauges, helper-process sampling, and incident-calibrated policy are
evidence inputs rather than a library API to preserve.
The reusable design should port the contracts and replay corpus to macOS and Linux, then
validate one shared policy over normalized evidence.

## Guard Lessons Carried Into the Design

The [memory guard](https://gist.github.com/jlevy/5b43e0d44166b9c7fe8157ee938cb0d5) is
the fifth version of its script; its README records the failure that produced each
mechanism. The mechanisms below are carried into the plans as invariants or provider
requirements, with the guard evidence that justifies each:

| Mechanism | Guard evidence | Where the plan carries it |
| --- | --- | --- |
| Measured evidence may take work away; predictive evidence may only hold it back | Replayed over every journal, the projection opened danger episodes on five runs that completed and the compressor slope on four healthy runs; one build shed 75 workers aged 11–16 s inside their healthy startup spike | Safety principle 3; the `embargo` and `critical` states |
| Pause the producer before harvesting | Over 217 s one workload added 52 processes while the guard removed 5 | Sentinel duty; shedding order |
| Pause every spawner, not the root | In seven of nine pause windows the tree grew while the root was stopped, up to +10.9 GB in one window, because the producer was three levels deep | Invariant 25 |
| A pause is a capped duty cycle | A 30 s pause left four work units reaped by their own supervisor one second before resume | Invariant 24; defaults 8 s cap, 1.5 s service window |
| A critical alarm never counts as recovered | An earlier build resumed a producer into pressure 4 four times in one run | Invariant 26; `critical` exit rule |
| Fault is attributed before any victim is taken, and recomputed every sample | One build killed a 40-unit batch after correctly naming unrelated processes as the cause; another shed its own workers first and asked whose fault it was once nothing was left | Shedding section |
| Abort needs a failing host and exhausted shedding, together | Aborting on exhausted rounds alone killed four consecutive runs with 6 GB reclaimable; not aborting at all ended in a kernel panic after 93 s without watchdog check-ins | Invariant 27 |
| The swap volume is a memory trigger | `no_paging_space_action` suspends one application every 5 s when the boot volume cannot hold another swapfile; a host sat at 12 GB of swap flapping into red with 6 GB of disk left | `critical` evidence; macOS provider |
| Kill the tree, not the PID, and stop before you walk | A compute-bound child holding 300 MB survived indefinitely at ppid 1 after its parent died; a running parent forks faster than an enumeration | Shedding mechanics |
| `killpg` is unsafe from outside | A parent, its grandchild, and the shell that started them shared one process group | Monitored-mode containment |
| Zombies hold no memory | Counting one inflates the tree and offering it as a victim wastes a round | Invariant 28 |
| Do not fork to measure | `host_statistics64` and `proc_pid_rusage` cost 24 µs and 0.2 ms against 313 ms for the helper commands; `fork` is the call that waits under pressure | Sentinel self-health; launch primitive |
| Raise the sentinel’s own priority, and know its limit | `THREAD_PRECEDENCE_POLICY` reaches 63; QoS is clamped by a per-task ceiling; the free-page wait queue is FIFO below priority 96 | Sentinel self-health |
| Lateness is diagnosis, never a trigger | A build that promoted lag to a trigger fed itself and shed four times at pressure 1 with 12 GB free | Invariant 29 |
| Size rounds by memory, not count | Fifty equal workers lose five; fifty where one holds a quarter lose only that one | Shedding mechanics |

## Lessons From Procguard v1.5.1

A static source review of
[Procguard v1.5.1](https://github.com/denispol/procguard/tree/v1.5.1) at commit
`36a16da` provides an implementation and test reference for the owned-process layer.
Procguard is a small macOS supervisor for one command, not a host-wide admission
controller or concurrent pool.
Its strongest choices are an atomic `posix_spawn` launch into a new process group,
`kqueue` exit and timer events, separate continuous and active clocks,
physical-footprint sampling through `proc_pid_rusage`, terminal usage from `wait4`, a
versioned JSON result, and explicit tests for fast-exit and signalling races.
The source locations were verified during the 2026-09-02 review; the project was read
statically and neither built nor executed.

| Procguard behavior | Source | Lesson for the standalone runtime |
| --- | --- | --- |
| The ordinary path uses `posix_spawnp`, but enabling a resource limit switches to `fork`. On macOS the attempted `RLIMIT_AS` limit returns `EINVAL`, which is swallowed, so memory enforcement still comes from a 100 ms poll. | `process.rs:346-465`, `rlimit.rs:91-110` | Safety options must not make launch less safe under memory pressure. Keep the supervising process on a nonforking path; if a limit must be applied before the target starts, spawn a minimal wrapper that applies it and then calls `exec`. |
| Memory and CPU polling read only the root PID, even though timeout signals target its process group. | `runner.rs:1797` | Every metric must declare `root`, `tree`, `cgroup`, or `host` scope. Root physical footprint is useful evidence but cannot be presented as process-group memory or used alone for group victim sizing. |
| Wall time uses `mach_continuous_time`, while active time excludes system sleep. | `runner.rs:296-330`, `wait.rs:61-95` | Every lease, grace period, startup window, and timeout needs an explicit clock domain. A startup reservation must not expire merely because the Mac slept while the target could not finish starting. |
| A v1.5.1 fix was needed because a zero wall timeout bypassed memory, throttle, heartbeat, and stdin monitors. | `runner.rs:812-829` | Governors compose independently. Disabling or zeroing one deadline must never bypass another configured monitor or the host authority. |
| The memory limit is not enforced during the `--kill-after` grace period. | `runner.rs:1106`, `runner.rs:1216` | Sampling continues through grace and settle windows; the guard keeps sampling through its settle interval for the same reason. |
| A `SIGSTOP`ped child is resumed before `SIGTERM` because a stopped process cannot run its handler. | `runner.rs:1194-1200` | Order the resume around the signal; the guard signals first and resumes after, and either order is acceptable as long as one is chosen. |
| Signal forwarding is a process-global self-pipe. Its cleanup contract forbids concurrent runs and resets handlers to `SIG_DFL` rather than restoring the application’s configuration. | `runner.rs:63-216` | `SafeProcess` must support many concurrent instances and must not take implicit per-run ownership of process-global signals. Use one application-level signal router or an explicit caller-owned forwarding policy. |
| The raw child handle has no terminal cleanup guard; only the spawn-attribute wrappers implement `Drop`. | `process.rs` | Every post-spawn exit path must either reap and clean the group or atomically transfer it to the broker or sentinel before returning. Supervisor failure cannot orphan the workload it was meant to contain. |
| The CLI and Rust library share one runner, and results are nonexhaustive and schema-versioned. The public configuration remains experimental and has accumulated timeout hooks, retries, file waiting, heartbeat, and throttling. | `runner.rs`, `lib.rs` | Keep one implementation behind the CLI and Python surfaces, use additive typed contracts, and resist moving orchestration conveniences into the safety boundary. Shell hooks, retry policy, and dependency waiting remain above it. |
| The test suite stresses immediate exits, `ESRCH` registration races, process-group signalling, zero-duration combinations, `SIGSTOP` cleanup, parsers, and timing arithmetic. Its 19 Kani proofs cover buffer bounds, time arithmetic, exit-status extraction, a once-cell, and throttle bookkeeping; they exclude the runner loop, FFI, and signal handler. | `tests/integration.rs`, `proc_info.rs`, `time_math.rs`, `process.rs`, `sync.rs`, `throttle.rs` | Reuse the failure corpus and layered test methods. State formal or model-checking coverage narrowly and require proofs to exercise shared production functions where possible. |

Procguard is therefore a reference, not a dependency candidate or replacement for the
proposed runtime. It validates the small native-supervisor shape and the CLI-and-library
seam, while its macOS-only, root-process, single-command contract reinforces the need
for separate host admission, cross-client identity, tree accounting, and Linux backends.

## Alternatives Considered

| Approach | Useful part | Why it is not the primary design |
| --- | --- | --- |
| Fix one downstream coordinator | Removes one incorrect fan-out | Other consumers and multiple independent runs can recreate the same host-wide burst. |
| Set a low static concurrency | Easy emergency brake | It wastes steady-state capacity, cannot represent startup peaks, and composes poorly across runs. |
| Add a fixed sleep before launch | Directly smooths a known transient | A per-process or per-coordinator delay is not host-wide and cannot react to outside load or mixed profiles. |
| Publish the current memory guard unchanged | Proven last-resort evidence, policy, and intervention | Its monitored-tree contract acts after launch, is macOS-only, and cannot provide authoritative cross-client admission. It should become one mode of the broader runtime. |
| Adopt Procguard directly | Small native macOS supervisor with strong owned-launch primitives and a valuable failure corpus | It governs one root process after launch, has no host-wide reservation or cross-client broker, uses process-global signal state, and has no Linux backend. Its code is a reference for the owned-process layer, not the required abstraction. |
| Publish a guard package and a separate pool before the process seam stabilizes | Keeps each repository superficially small | It creates two new boundaries before either has operating evidence and risks duplicating telemetry, identity, signalling, and journal policy. Ship the process-safety package first. |
| Delegate to GNU Parallel | Mature command queue with launch delay, memory admission, suspension, and retry | Its generic free-memory rules do not provide the required macOS pressure accounting, startup-phase claims, cross-client identity registry, or independent sentinel. |
| Publish Metaproc’s current RunPool unchanged | Reuses a capable scheduler and local process manager | The module mixes a potentially reusable pool core with Metaproc paths, lanes, events, status, provider policy, and artifact compatibility. Prove a neutral slice before deciding whether to extract it. |
| Extract the full pool before proving the process seam | Creates a clean-looking package boundary early | It risks lockstep releases and callback abstractions that mirror Metaproc internals. Extract the core and owned-process boundary first, then apply the pool gate. |
| Use only OS hard limits | Strong Linux containment | macOS has no equivalent local cgroup boundary, and a hard byte ceiling alone can kill healthy startup transients. |
| Keep the current adaptive semaphore | Good local throughput controller | Every pool sees only its own capacity, reduction is non-preemptive, and the scalar memory estimate misses the burst shape. |

GNU Parallel demonstrates that a standalone command pool can use both
[launch spacing and memory-aware admission](https://www.gnu.org/software/parallel/parallel.html).
The standalone process-safety runtime keeps those useful mechanics while adding the
platform-specific evidence, cross-client ownership, and failure isolation required
there.

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
- [Linux `pidfd_open`](https://man7.org/linux/man-pages/man2/pidfd_open.2.html)
- [Linux `prctl` subreaper](https://man7.org/linux/man-pages/man2/prctl.2.html)
- [Memory accounting reference](../../memory-accounting-reference.md)
- [Procguard v1.5.1](https://github.com/denispol/procguard/tree/v1.5.1)
- [GNU Parallel memory and launch controls](https://www.gnu.org/software/parallel/parallel.html)
- [Agent CLI Startup Memory](research-2026-09-01-agent-cli-memory-usage.md)
- [RunPool host-safety plan](../specs/active/plan-2026-09-01-runpool-host-safety.md)
- [Safeproc local-incubation plan](../specs/active/plan-2026-09-01-safeproc-local-incubation.md)
- [Standalone macOS memory guard](https://gist.github.com/jlevy/5b43e0d44166b9c7fe8157ee938cb0d5)

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
