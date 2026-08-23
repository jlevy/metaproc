# Memory Accounting Reference

Background for anyone changing how Metaproc sizes concurrency, and the citations behind
the choices in [arch-runpool.md](arch/arch-runpool.md).

Memory is the binding constraint on agent fan-out.
A single agent step is not a single process, the per-process cost is not what the
obvious counter reports, and on macOS the obvious counter for *host* headroom is off by
roughly a factor of two in the unsafe direction.
Each of those has produced a wrong decision in practice, so each is written down here
with the source that settles it.

Every measurement below was taken on a 34.36 GB Apple Silicon host and is reproducible
with the commands given.

## The Short Version

| Question | Wrong answer that looks right | Correct source |
| --- | --- | --- |
| How much host memory is free? (macOS) | `kern.memorystatus_level` | `vm_stat` free + inactive + purgeable |
| How much host memory is free? (Linux) | `MemFree` | `MemAvailable` |
| What does one process cost? (macOS) | `ps` RSS | `phys_footprint` |
| What does one process cost? (Linux) | RSS | `smaps_rollup` PSS |
| What does a fan-out cost? | sum of per-process RSS | sum of footprint or PSS over each process tree |
| Is the host degrading? | any memory level | stall time, or paging rate |

## macOS: the Pressure Gauge Counts Other Processes’ Working Sets

`kern.memorystatus_level` is the number `memory_pressure` prints as a free percentage,
and it is the natural thing to reach for.
It is a pressure signal, not a budget.

In XNU it is fed by `AVAILABLE_NON_COMPRESSED_MEMORY`
([osfmk/vm/vm_page.h](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/vm/vm_page.h)):

```c
#define AVAILABLE_NON_COMPRESSED_MEMORY  (vm_page_active_count + vm_page_inactive_count + vm_page_free_count + vm_page_speculative_count)
#define AVAILABLE_MEMORY                 (AVAILABLE_NON_COMPRESSED_MEMORY + VM_PAGE_COMPRESSOR_COUNT)
#define VM_CHECK_MEMORYSTATUS            memorystatus_update_available_page_count(AVAILABLE_NON_COMPRESSED_MEMORY)
```

`vm_page_active_count` is in that sum.
Active pages are the resident working sets of every other running process.
Counting them as available says a new process can have memory that a running one is
currently using.

Two consequences, and the second is the one that bites:

- **It reads about twice the reclaimable figure.** Solving for which `vm_stat` buckets
  reproduce the gauge on a live host: free + inactive + active + speculative + purgeable
  came to 47.43% against a gauge reading of 48.0%, while free + inactive + purgeable
  came to 22.60%. The kernel macro and the arithmetic on live counters agree.
- **It falls late.** Nothing moves it until the compressor has already grown, because
  compression happens before the page counts it sums are disturbed.
  A host reading a comfortable 48% was holding 67 MB free and 12.07 GB compressed.

Reproduce both:

```sh
sysctl -n kern.memorystatus_level
vm_stat | awk '/page size of/{gsub(/[^0-9]/,"",$8);ps=$8}
  /Pages free/{gsub(/\./,"",$3);f=$3} /Pages inactive/{gsub(/\./,"",$3);i=$3}
  /Pages purgeable/{gsub(/\./,"",$3);p=$3}
  END{printf "reclaimable %.2f GB\n",(f+i+p)*ps/1e9}'
```

Metaproc budgets from the second command and keeps the first as
`MemoryPressure.alarm_pct`. Note that the page size is read from the `vm_stat` header
rather than assumed: Apple Silicon reports 16384 and Intel reports 4096, and hardcoding
either is a 4x error on the other.

One trap for anyone moving from `vm_stat(1)` to the binary API. In `vm_statistics64`
(SDK `mach/vm_statistics.h`) the header states:

```c
natural_t  free_count;             /* # of pages free */
/* NB: speculative pages are already accounted for in "free_count" */
```

`vm_stat(1)` prints “Pages free” and “Pages speculative” as separate, disjoint lines,
having already subtracted the latter.
Summing both from `host_statistics64` double counts what summing both from `vm_stat`
output does not.

## macOS: RSS Is Wrong in Both Directions

The per-process cost metric is `phys_footprint`, defined in
[osfmk/kern/task.c](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/kern/task.c),
`init_task_ledgers()`:

```c
/*
 * phys_footprint
 *   Physical footprint: This is the sum of:
 *     + (internal - alternate_accounting)
 *     + (internal_compressed - alternate_accounting_compressed)
 *     + iokit_mapped
 *     + purgeable_nonvolatile
 *     + purgeable_nonvolatile_compressed
 *     + page_table
 */
```

It is what Activity Monitor shows as Memory and what jetsam limits are enforced against.
Against it, RSS errs in both directions at once:

- **RSS understates a single process**, because footprint counts compressed pages and
  IOKit mappings and RSS counts neither.
  Measured on a live node process: 58.4 MB RSS against 91 MB footprint.
  On a host with 12 GB in the compressor that gap is structural, not noise.
- **RSS overstates a fan-out**, because summing it across N processes counts each shared
  page N times. N agent CLIs sharing a binary, a V8 snapshot, and system libraries have
  that text counted once per process.

So neither summing RSS nor summing RSS with a sharing discount converges on the truth;
the two errors are independent and point opposite ways.
Read `phys_footprint` per process and sum that.

Also worth sampling: the same process reported `phys_footprint_peak` of 361 MB against a
91 MB steady state. A budget built from steady-state samples underestimates what a
fan-out transiently needs by whatever that ratio happens to be.

```sh
footprint -p <pid>          # phys_footprint and phys_footprint_peak
ps -o rss= -p <pid>         # the number that disagrees
```

Binary API: `task_info(task, TASK_VM_INFO, ...)` returns `task_vm_info.phys_footprint`
(SDK `mach/task_info.h`).

## Linux: MemAvailable and PSS

`MemAvailable` is the budget, and the kernel computes it precisely so that callers stop
estimating it themselves.
From the commit that added it
([34e431b0ae39](https://github.com/torvalds/linux/commit/34e431b0ae398fc54ea69ff85ec700722c9da773)),
implemented today in
[mm/show_mem.c](https://github.com/torvalds/linux/blob/master/mm/show_mem.c) as
`si_mem_available()`: it accounts for reclaimable page cache and slab, minus watermarks,
rather than reporting only `MemFree`. Metaproc reads it directly.

For per-process cost, `/proc/<pid>/smaps_rollup` reports PSS, which divides each shared
page by its sharer count, making it the number that answers “how much would killing this
free”. `VmRSS` decomposes into `RssAnon + RssFile + RssShmem`; the file-backed part is
the shared text that makes summed RSS overcount.
See the
[kernel proc documentation](https://www.kernel.org/doc/html/latest/filesystems/proc.html).

macOS has no PSS equivalent, which is why the two platforms need different per-process
metrics rather than one portable one.

## The Degradation Signal Is Stall Time

No memory level predicts degradation on its own.
A host with little free memory and no paging is fine; a host with moderate free memory
and rising paging is already slowing down.
What matters is time lost waiting on memory.

Linux exposes this directly as PSI
([kernel PSI documentation](https://www.kernel.org/doc/html/latest/accounting/psi.html)):
`/proc/pressure/memory` reports `some avg10` as the share of the last ten seconds that
at least one task spent stalled on memory.
Metaproc reads it and, above 5, lets it override the `MemAvailable` percentage when it
implies more pressure.

macOS has no PSI. It also has no *leading* signal wired in Metaproc today, which is a
known gap rather than a design choice: the compressor absorbs pressure before swap
moves, so `vm.swapusage` lags the condition it is meant to warn about.
Compressor page count is the candidate signal.
Note also that `swapins` and `swapouts` in `vm_statistics64` are lifetime totals, so any
use of them must take deltas.

## Why This Keeps Being Got Wrong

The counters that are easiest to reach are the ones that mislead, and they mislead in
the reassuring direction.
`memory_pressure` prints a big friendly percentage.
`ps` prints RSS. Both are one command away, both look authoritative, and both say there
is more room than there is.
The correct sources take an extra step in every case.

Two recorded consequences, kept here because the reasoning that produced them was
careful and still wrong:

- An operator watching a gauge report 73-86% free concluded the per-process cost
  estimate was off by an order of magnitude and proposed tripling fan-out.
  Reclaimable memory at that moment was 1.81 GB, and the existing fan-out was already at
  the budget.
- Sizing a batch from the same gauge exhausted a host twice in one day.
  The gauge read 87-90% in the seconds before each.

Knowing about the trap is not protection from it, because the wrong number remains
available and continues to look right.
Read the correct source every time, including when the answer seems obvious.

## What Metaproc Does Today

| Concern | Implementation | Status |
| --- | --- | --- |
| Budget, macOS | `vm_stat` free + inactive + purgeable over `hw.memsize` | correct |
| Budget, Linux | `MemAvailable` from `/proc/meminfo` | correct |
| Pressure alarm, macOS | `kern.memorystatus_level` as `alarm_pct` | correct, and never used as a budget |
| Degradation, Linux | PSI `some avg10`, plus swap-rate grading | correct |
| Degradation, macOS | `vm.swapusage` only | lags; compressor growth is unread |
| Per-process cost | `PsutilSampler` sums RSS over the process tree | wrong metric, and wired only on the `mode: code` path |
| Re-decision | `_adjust_concurrency` scales the ceiling by pressure level | never re-derives capacity from bytes |
| Admission | `_acquire_host_slot` leases a counted slot | no memory gate |

The last three rows are open.
They are the reason `estimated_process_rss_bytes` is a configured guess rather than a
measurement, and the reason a ramp can climb past physical memory whenever the pressure
signal lags.

## References

- XNU sources:
  [osfmk/vm/vm_page.h](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/vm/vm_page.h),
  [osfmk/kern/task.c](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/kern/task.c),
  [bsd/kern/kern_memorystatus.c](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_memorystatus.c)
- Local SDK headers: `mach/vm_statistics.h`, `mach/task_info.h`
- Linux:
  [MemAvailable commit](https://github.com/torvalds/linux/commit/34e431b0ae398fc54ea69ff85ec700722c9da773),
  [mm/show_mem.c](https://github.com/torvalds/linux/blob/master/mm/show_mem.c),
  [proc filesystem](https://www.kernel.org/doc/html/latest/filesystems/proc.html),
  [PSI](https://www.kernel.org/doc/html/latest/accounting/psi.html)
- Commands: `vm_stat(1)`, `footprint(1)`, `memory_pressure(1)`, `sysctl(8)`
- [arch-runpool.md](arch/arch-runpool.md) for how these feed the adaptive controller

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
