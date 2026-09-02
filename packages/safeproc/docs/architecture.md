# Safeproc Architecture

Safeproc is one implementation with several small surfaces.
The policy it implements is specified by the
[RunPool Host Safety Envelope](../../../docs/project/specs/active/plan-2026-09-01-runpool-host-safety.md);
the package boundary, phases, and quality gates are specified by the
[Safeproc Local Incubation](../../../docs/project/specs/active/plan-2026-09-01-safeproc-local-incubation.md)
plan. This document describes what exists in the tree today.

## Layers

```text
safeproc CLI (cli.py)
        ├── watch ── ProcessMonitor (monitor.py) ─────┐
        └── replay ── journal replay (replay.py) ─────┤
                                                       │
         policy.py  journal.py  identity.py  models.py  clocks.py
                                                       │
                       _platform/linux.py   _platform/darwin.py
```

Import direction is enforced: nothing in this package imports `metaproc`, and the pure
modules (`models`, `policy`, `identity`, `clocks`, `journal`, `replay`) import no
platform code. `ProcessMonitor` depends on the provider contract in `_platform/base.py`,
never on a concrete provider; the CLI selects the provider at run time.

| Module | Owns |
| --- | --- |
| `models.py` | Scoped host and tree samples, the guard policy and its calibrated defaults, danger reasons split into measured and predictive, pressure states, actions, decisions |
| `policy.py` | The pressure engine: triggers, confirmation, the pause duty cycle, proportional shedding, fault attribution, the two-condition abort |
| `identity.py` | PID plus creation token, tree reconstruction, spawner and deepest-first orderings, identity fencing |
| `clocks.py` | Active and sleep-aware clock domains and deadlines that name theirs |
| `journal.py` | Versioned JSONL records, redaction, the tally and summary |
| `replay.py` | A journal back through a fresh engine, with drift detection |
| `monitor.py` | `ProcessMonitor` and `MonitoredProcess`, the producer pause, tree termination |
| `launch.py` | The owned-launch primitive: `posix_spawn` into a new session, the wrapper handshake, exit observation through `pidfd` or `kqueue` |
| `_launch_wrapper.py` | The minimal wrapper that registers, handshakes, and `exec`s the target |
| `_platform/base.py` | The provider protocol and capability record |
| `_platform/linux.py` | procfs and cgroup v2 evidence, no helper commands |
| `_platform/darwin.py` | libSystem evidence ported from the memory guard, with its helper fallbacks |

## Modes

Monitoring is the only mode implemented so far.
It fences a target by PID and creation token, samples the host and the tree, runs the
engine, and journals every decision.
Under `--policy observe`, the default, it never signals; the journal still records what
the guard policy would have done, marked `observed_only`. Under `--policy guard` it
pauses spawners, sheds proportionally, and aborts only when the host is measurably
failing and shedding is exhausted.
`--dry-run` decides and journals without signalling.

Owned launch, the broker, and the sentinel are later phases.
The launch primitive they need is in `launch.py` and proved on Linux by
`tests/unit/test_launch.py` (bead `mp-t9u5`): `os.posix_spawn` with a new session and no
fork of the supervisor, a handshake pipe that distinguishes a wrapper death from a
target exit, and exit observed through a `pidfd` reader on the event loop with twelve
concurrent launches sharing one loop.
The `kqueue` path is the macOS equivalent and is part of the handoff below.

## Platform capabilities

| Capability | Linux | macOS |
| --- | --- | --- |
| Host budget | `MemAvailable`, bounded by the caller’s own cgroup headroom | `host_statistics64` free + inactive + purgeable |
| Alarm | derived from PSI stall averages and available fraction (uncalibrated) | `kern.memorystatus_vm_pressure_level` |
| Predictive signals | `some` stall, reclaimable slope, swap-in rate | compressor growth, reclaimable slope |
| Measured signals | sustained `full` stall, floor | alarm, floor, swap-volume suspension line, red-line ratio |
| Process cost | `smaps_rollup` PSS behind the accuracy gate | `proc_pid_rusage` physical footprint |
| Identity | `/proc/<pid>/stat` starttime | `proc_bsdinfo` start time (native) or `ps` start second (fallback) |
| Sampling | native, no helper commands | native through libSystem; helper fallbacks retained |
| Sleep-aware clock | `CLOCK_BOOTTIME` | `CLOCK_MONOTONIC` |
| Sentinel priority | none available unprivileged | `THREAD_PRECEDENCE_POLICY` 63 |

Windows is deferred indefinitely as a future phase; see the system plan.

## Provenance

The policy, defaults, journal shape, and macOS readings are adapted from the
[memory guard](https://gist.github.com/jlevy/5b43e0d44166b9c7fe8157ee938cb0d5) at gist
commit `d5e67ea`, the author’s own code.
Its README records the failure behind each mechanism; `models.py` carries the rationale
on each field.
Procguard v1.5.1 was read statically for the owned-launch layer and is not
copied.

## macOS handoff

`_platform/darwin.py` was written on Linux and has not run on macOS. The native macOS
agent should, in order:

1. Run `make safeproc-test`. The test `test_native_readings_are_plausible` in
   `tests/unit/test_darwin_parsing.py` is the first check and is skipped elsewhere.
2. Verify every `ctypes` layout against the SDK headers: `vm_statistics64`
   (`mach/vm_statistics.h`), `rusage_info_v4` (`sys/resource.h`), `xsw_usage`
   (`sys/sysctl.h`), and `proc_bsdinfo` (`sys/proc_info.h`, 136 bytes).
   The first three are the guard’s and were measured; `proc_bsdinfo` is new here.
3. Cross-check `host_memory()` against `vm_stat`, `footprint_mb()` against `footprint`,
   `swap_usage()` against `sysctl vm.swapusage`, and `process_table()` against `ps`,
   including `create_token` stability across two samples of the same process.
4. Confirm `harden_scheduling()` reports `hardened` and that measured thread priority is
   63, as the guard measured.
5. Run the live tests in `tests/integration/test_live_processes.py`, which exercise
   pause, resume, and deepest-first termination on a real subtree, and
   `tests/unit/test_launch.py`, which exercises the `kqueue` exit path and
   `POSIX_SPAWN_SETSID` on Darwin.
6. Replay the guard’s sanitized journal corpus once it is exported (incubation plan,
   Supply Chain section) and confirm zero destructive actions on the runs that
   completed.

Anything that fails in step 2 is a layout bug in this port, not a policy question.
