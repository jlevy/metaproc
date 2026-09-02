---
title: Review of the Process-Safety Plans (Pull Request 62)
description: >-
  Senior engineering review of the RunPool Host Safety Envelope and Safeproc Local
  Incubation plans, checked against the macOS memory guard, Procguard v1.5.1, and the
  current code, with a platform-by-platform assessment for macOS, Linux, and Windows.
author: Claude Code review for Joshua Levy (github.com/jlevy)
date: 2026-09-02
status: Review — awaiting plan revision
category: review
tracking_bead: mp-sbue
---
# Review: Process-Safety Plans (Pull Request 62)

**Pull request:** [jlevy/metaproc#62](https://github.com/jlevy/metaproc/pull/62), head
`5f639e1`, base `10f5185`, eight files, +2,698/−2, documentation only.
`main` was at `c101f86` when this review was written; the PR reports mergeable with no
conflict, and all five CI jobs passed on the head commit.

**Documents under review:**

- `docs/project/specs/active/plan-2026-09-01-runpool-host-safety.md` (1,363 lines), the
  system plan
- `docs/project/specs/active/plan-2026-09-01-safeproc-local-incubation.md` (754 lines),
  the package plan
- `docs/project/research/research-2026-09-01-host-memory-accounting-and-control.md`
- `docs/project/research/research-2026-09-01-agent-cli-memory-usage.md`
- index edits to `TODO.md`, `docs/project/README.md`, `docs/project/research/README.md`,
  and `docs/project/design/backlog/arch-runpool-backlog.md`

**Reference material checked against the plans:**

- the
  [macOS memory guard](https://gist.github.com/jlevy/5b43e0d44166b9c7fe8157ee938cb0d5)
  at gist commit `d5e67ea` (2026-08-29): `memory_guard.py` (3,190 lines),
  `test_memory_guard.py` (1,245 lines, 96 tests), and its README
- [Procguard](https://github.com/denispol/procguard) at tag `v1.5.1` (commit `36a16da`);
  the current `main` differs only in two CI workflow files, so the reviewed version is
  the current source
- the tbd graph: epic `mp-bd6v`, feature `mp-qigc`, and the ten implementation beads the
  package plan lists

Both reference checkouts were placed under the git-ignored `attic/reference/` directory
of this checkout with a `PROVENANCE.md` recording the commits.
They are not committed; the appendix gives the commands to recreate them.

## Verdict

**Approve the direction.
Request one revision round before merge, and three follow-up beads before implementation
starts.**

The architecture conclusion is right and well argued: one safety core, two
non-substitutable supervision modes (owned launch versus monitoring an existing tree), a
brokerless `watch`, a per-user broker that doubles as the sentinel, and no pool in the
first package.
The Procguard lessons table is accurate in every row I checked against the
source. The research records are careful about what is causal and what is a single
measurement.

Four things keep this from being ready as written:

1. **The two plans overlap enough to drift.** They each carry a layer diagram, a Python
   surface table, a CLI listing, acceptance criteria, open questions, and a phase plan,
   and the two phase plans do not agree with each other.
2. **The “nonforking owned launch” requirement is asserted, not designed.** CPython’s
   `subprocess` cannot satisfy it with `start_new_session=True`, which is exactly how
   Metaproc launches today.
   This is the single most consequential gap for Phase 3.
3. **Several hard-won guard lessons are missing or contradicted.** The pause duty cycle,
   spawner-wide pausing, the swap-volume suspension line, and the sentinel’s own
   scheduling health are absent, and one non-goal reads as rejecting a trigger the guard
   proved.
4. **Linux is a gauge list, not a plan, and Windows is a non-goal.** macOS has a failure
   corpus, calibrated thresholds, and a replay set.
   Linux has none of those, and the plan omits the mechanisms that would make Linux
   containment stronger than macOS. The user’s stated goal includes Windows, which both
   plans currently decline.

The rest of this document gives the evidence, the findings in severity order, a platform
assessment, and a concrete list of edits.

## What Was Verified

Claims the plans make about the current Metaproc code, checked at `main` (`c101f86`):

| Plan claim | Verified at | Result |
| --- | --- | --- |
| Scalar admission launches without a slot after a timeout or `OSError` | `src/metaproc/runpool/scalar_admission.py:87-96` | Confirmed; the module docstring calls this deliberate |
| Per-process health sums RSS across the tree | `src/metaproc/runpool/backend.py:406-411` | Confirmed; `memory_info().rss` summed over `children(recursive=True)` |
| macOS sampling forks `vm_stat` and `sysctl` | `src/metaproc/osutils/memory_pressure.py:141-160` | Confirmed; `subprocess.run` with a 5 s timeout on every sample |
| Linux PSI is folded into an inverted “available” percentage | `src/metaproc/osutils/memory_pressure.py:262-276` | Confirmed; `100 - some_avg10 × 2` replaces `MemAvailable` when lower |
| Gemini adapter accepts `no_session_persistence` and does not consume it | `src/metaproc/adapters/gemini.py:119` | Confirmed; the key is only in the allowed-key set. `claude_code.py:560` consumes it, `gemini.py` never reads it |
| Windows is unsupported today | `src/metaproc/docs/arch-runpool.md:122` | Confirmed |
| Launch uses `start_new_session=True` and `killpg` | `src/metaproc/runpool/backend.py:270-277, 357, 374` | Confirmed |

Claims the plans make about Procguard, checked at `v1.5.1`:

| Plan claim | Verified at | Result |
| --- | --- | --- |
| Ordinary path is `posix_spawnp`; enabling a limit switches to `fork` | `src/process.rs:346-408` and `412-465` | Confirmed |
| `RLIMIT_AS` is rejected on macOS and silently skipped | `src/rlimit.rs:91-110` | Confirmed; `EINVAL` is swallowed, memory enforcement is a 100 ms poll |
| Memory polling reads only the root PID | `src/runner.rs:1797` | Confirmed; `get_process_memory(pid)` on the root while signals go to the group |
| Wall clock is `mach_continuous_time`; active clock excludes sleep | `src/runner.rs:296-330`, `src/wait.rs:61-95` | Confirmed |
| Zero wall timeout once bypassed the other monitors | `src/runner.rs:812-829` | Confirmed; v1.5.1 gates the fast path on `needs_runtime_monitoring` |
| Signal forwarding is process-global | `src/runner.rs:63-216` | Confirmed; two static atomics, six `sigaction` calls, reset to `SIG_DFL` on cleanup |
| The raw child handle has no terminal cleanup guard | `src/process.rs` | Confirmed; only `SpawnAttr` and `SpawnFileActions` implement `Drop` |
| Kani scope excludes the runner loop, FFI, and signal handler | `src/*.rs` | Confirmed; the 19 proofs cover `proc_info` buffer bounds, `time_math`, exit-status extraction, `sync`, and `throttle` |

Two Procguard facts the plans do not mention and should:

- The memory limit is **not enforced during the `--kill-after` grace period**
  (`src/runner.rs:1106` and `1216`). The guard keeps sampling through its settle window;
  Safeproc must too.
- Procguard resumes a `SIGSTOP`ped child **before** sending `SIGTERM` because a stopped
  process cannot run its handler (`src/runner.rs:1194-1200`). The guard does the same in
  the other order: `SIGTERM` the subtree, then `SIGCONT` the root
  (`memory_guard.py:2200-2210`). Either order works; the plan says neither.

The tbd graph exists and matches the package plan’s table: epic `mp-bd6v` has the ten
children `mp-bu84` through `mp-rcct` with the listed titles, and `mp-qigc` is the
in-progress umbrella feature.

## Findings

Severity is about what the omission would cost once code is written, not about the size
of the edit.

### F1. The two plans duplicate each other and disagree on phases

The system plan has “Phase 0: Prove the Reusable Boundary” through “Phase 4: Evaluate
Pool Extraction”, with a twelve-item Phase 0 checklist that covers package mechanics.
The package plan has “Phase 0: Land the Design and Research” through “Phase 5: Separate
Repository and Metaproc Adoption” with different content under the same numbers.
A reader cannot tell which sequence the beads implement.
The beads follow the package plan.

Both documents also carry: a concentric-layer table or diagram, a Python surface table
(`SafeProcess`, `ProcessTarget`, `ProcessMonitor`, `MonitoredProcess`), a CLI listing,
acceptance criteria, an open-questions list, and a Procguard boundary statement.
Duplicated normative text is the mechanism by which two plans drift into contradiction,
and the dependency-direction diagrams already differ in detail (the system plan draws
the broker as a peer of `SafeProcess`; the package plan draws `run` through a broker
client).

**Fix.** Give each document one job and say so at the top of both:

- the system plan owns policy: invariants, the resource model, the pressure state
  machine, shedding, Metaproc integration, and rollout gates;
- the package plan owns the package: layers, public types, CLI, quality gates, workspace
  mechanics, extraction, and the phase list that the beads implement.

Delete the duplicated tables from the document that does not own them and link instead.
Keep one phase numbering.
If the system plan needs a checklist, name its items by architectural outcome and point
each at a bead.

### F2. “Nonforking owned launch” has no Python design behind it

Both plans require that the supervising parent never `fork` on the authoritative launch
path (invariants 21 and the acceptance criterion “The supervising parent does not call
`fork` on the authoritative launch or sampling path”). That is the right requirement;
the guard measured `fork` as the operation that waits under pressure.

CPython cannot meet it through `subprocess` in the configuration Metaproc needs.
In Python 3.12 `subprocess.py:1825-1839`, `posix_spawn` is used only when all of these
hold: no `preexec_fn`, `close_fds=False`, no `pass_fds`, no `cwd`, stdio not redirected
to low descriptors, `start_new_session=False`, no `process_group`, no uid or gid change,
and no umask. Metaproc’s `LocalBackend` sets `start_new_session=True` and relies on the
default `close_fds=True`, so every agent launch today goes through
`_posixsubprocess.fork_exec`. On Linux that path uses `vfork` where it can; on macOS it
is a real `fork` of the parent.
`asyncio.create_subprocess_exec` wraps the same `Popen`.

The package plan says “Use `posix_spawn`-class launch, a minimal child wrapper when
pre-exec setup is unavoidable” and stops there.
What is missing:

- **The spawn call.** `os.posix_spawn` supports `setsid=True` (`POSIX_SPAWN_SETSID` on
  macOS, `POSIX_SPAWN_SETSID` on glibc 2.26+) and `setpgroup`, so an isolated session
  and group can be created without a fork.
  It does not support `close_fds`; the plan must state that descriptor hygiene comes
  from PEP 446 non-inheritable descriptors plus explicit `file_actions`, and that macOS
  `POSIX_SPAWN_CLOEXEC_DEFAULT` is not reachable from Python.
- **Exit observation without `SIGCHLD`.** `asyncio` child watchers assume `Popen`. The
  owned path needs its own waiter: `pidfd_open` plus the event loop’s reader on Linux
  5.3+, and `select.kqueue` with `EVFILT_PROC`/`NOTE_EXIT` on macOS, falling back to a
  waiter thread. Both are standard library.
- **Where the wrapper’s registration happens.** The plan’s wrapper “obtains a host
  claim, creates a new session and process group, records its identity with the broker,
  and only then replaces itself with the target”.
  That is correct, and it also means the claim handshake runs inside a process that is
  not the supervisor. The plan should say what the supervisor waits on (a pipe or the
  broker’s event) to learn that registration completed before it reports the launch as
  owned, and what happens when the wrapper dies between spawn and `exec`.
- **Sampling.** Invariant 19 forbids helper subprocesses on the sampling path.
  The guard already proved `host_statistics64` and `proc_pid_rusage` through `ctypes`
  (24 µs and 0.2 ms for a 60-process tree, versus 313 ms forking `vm_stat`). The plan
  cites this but does not state that Metaproc’s existing `memory_pressure.py` is
  replaced, not wrapped.

**Fix.** Add a short “Launch primitive” section to the package plan covering the four
bullets, and open a spike bead ahead of `mp-3c0g` that proves `os.posix_spawn` plus a
stdlib exit waiter on both platforms under `asyncio`. This is the riskiest unknown in
the plan and it is cheap to retire early.

### F3. Guard lessons that are absent or contradicted

The guard README calls itself the fifth version and lists what each earlier version got
wrong. The plans absorb most of it: measured-versus-predictive authority, wall-clock
confirmation, cadence lag as diagnosis only, fault attribution, nonforking sampling,
identity fencing, deepest-first termination, one round per settle window.
These are the ones that did not make it.

**F3a. The pause is a duty cycle, and the plan has no cap.** `--max-pause-s` (8 s) and
`--min-run-s` (1.5 s) exist because a producer frozen past its children’s deadlines got
four work units reaped by their own supervisor (`memory_guard.py:2032-2060`,
`TestDutyCyclePause`, `TestPauseIsBounded`). The system plan says the sentinel must
“always resume any producer it stopped” but never bounds how long a pause may last.
An unbounded pause is a correctness bug in owned mode too: Metaproc’s own step timeouts
keep running while its launcher is stopped.
Add an invariant: every producer pause has a wall-clock cap and a minimum service
window, and the cap is part of the persisted policy.

**F3b. Pause every spawner, not the root.** Root-only pausing leaked +10.9 GB in one
window because the producer was three levels deep (`ProducerPause._spawners`,
`TestSpawnerWidePause`). The system plan says “stop registered producer PIDs” for owned
mode, where the registered producers are Metaproc parents, which is adequate.
For monitored mode it says “producer pauses” with no tree semantics.
State that a monitored-mode pause freezes every non-leaf in the fenced tree and
re-freezes intermediates born since.

**F3c. Pressure 4 never counts as recovered.** An earlier guard build resumed into
kernel pressure level 4 four times because reclaimable memory looked adequate
(`TestPressureFourNeverRecovers`). The guard also requires five consecutive clear
samples before resuming.
The plan mentions hysteresis only in the test list.
Put the rule in the state machine: `critical` exits only when the platform alarm is
clear and headroom has held for a confirmation window.

**F3d. The swap-volume suspension line is a measured trigger, and one non-goal reads as
rejecting it.** The guard’s closest trigger to user-visible harm is `suspension_gb`,
disk free on the swap volume plus unused allocated swap, with danger at 4 GB and abort
eligibility at 1.5 GB (`Sample.suspension_gb`, `danger_reason`,
`TestSuspensionLineAndRatio`). Its README explains why: `no_paging_space_action`
suspends one application every five seconds once the boot volume cannot hold another
swapfile, so a full disk presents as a memory failure.
The system plan’s macOS provider bullets do say “track compressor growth and the
distance to swap-volume exhaustion separately from ordinary disk pressure”, and Phase 2
repeats it. But the Non-Goals section says “Turn a general disk-space warning into a
memory kill trigger”, and the pressure state table’s `critical` row lists “sustained
measured critical pressure, unsafe reclaimable headroom, or Linux full-stall evidence”
without the suspension line or the kernel red-line ratio (`ancm_ratio`, trigger at 0.40
of the 0.66 red line while under pressure).
Reword the non-goal to “ordinary low disk on a volume that does not hold swap”, and add
both macOS triggers to the `critical` evidence so they are measured evidence, not
predictions.

**F3e. The sentinel’s own scheduling.** The guard raises its thread to `MAXPRI_USER`
(63) with `THREAD_PRECEDENCE_POLICY` plus `THREAD_EXTENDED_POLICY{timeshare=0}`, and
documents why QoS and `setpriority` do not work (`harden_scheduling`,
`memory_guard.py:872-944`). It also documents the limit: priority cannot fix
free-page-wait starvation, only not forking can.
The plan’s sentinel section covers nonforking and cadence lag but has no “sentinel
self-health” paragraph.
Add one, with the priority call as a macOS capability and the honest statement of what
it does not buy.

**F3f. Zombies.** The guard drops zombies from the tree because they hold no address
space and offering one as a victim wastes a round (`_alive_any`, `snapshot`). The plan’s
accounting tests do not mention them.
Add to the deterministic corpus.

**F3g. Kill mechanics.** The guard’s `terminate_batch` (`memory_guard.py:2165-2226`)
encodes four rules: `SIGSTOP` the victim root before enumerating so it cannot fork
faster than the walk; signal deepest-first; share one grace deadline across the batch;
`SIGCONT` after `SIGTERM` so a stopped process can handle it.
The plan says “deepest-first” once, in the test list, and “proportional rounds” for
sizing. Put all four in the Design section for monitored mode, and state that owned mode
uses group signalling instead because it created the group.

**F3h. Abort is gated on two conditions, not one.** Exhausting shed rounds alone never
authorises taking the tree; the host must also be genuinely failing (`_must_abort`).
Both directions of that bar were paid for, one in four lost runs and one in a kernel
panic. The plan’s `catastrophic` row is close ("critical state is worsening, no
cooperative response, near the calibrated failure boundary") but does not say that spent
rounds alone are not catastrophic.
One sentence fixes it.

**F3i. The replay corpus and the hygiene rule.** Phase 1 of the package plan imports
“the sanitized memory-guard replay corpus”.
The corpus is nineteen journals from a downstream host, and `AGENTS.md` forbids copied
operational artifacts.
Say where the corpus lives, who sanitizes it, and what “sanitized” removes (hostnames,
paths, argv), or the first implementation bead will stall on the question.

### F4. Linux is a gauge list, not a plan

The macOS side of the plan rests on a failure corpus, calibrated defaults, and measured
sampling costs. The Linux side names the right files (`MemAvailable`, PSI,
`smaps_rollup`, cgroup v2) and stops.
The research record’s own open-evidence list ends with “Establish equivalent Linux
profiles with PSS or cgroup accounting”, and no bead does that.
Specific gaps:

- **`MemAvailable` is host scope and wrong inside a limited cgroup.** Cloud workers,
  Docker, and GCP Batch run under `memory.max` that is far below the host figure.
  The budget must be the minimum of host `MemAvailable` and the caller’s own cgroup
  headroom (`memory.max` minus `memory.current`).
  `src/metaproc/osutils/resource_context.py` already parses those files; the plan should
  reuse it rather than list cgroup files as optional refinements.
- **PSI is not always there and not always writable.** `/proc/pressure` is absent when
  `CONFIG_PSI` is off or `psi=0` is set, and often absent in containers.
  Creating a PSI trigger (write, then `poll`) required `CAP_SYS_RESOURCE` before kernel
  6.5; unprivileged triggers since 6.5 are limited to multiples of two-second windows.
  The cgroup-local `memory.pressure` in the caller’s own cgroup is readable without
  privilege. The plan says “preferably pollable threshold triggers” without the gating.
  Write the capability record for PSI as three states: absent, readable averages only,
  triggers available.
- **There is no compressor.** The guard’s compressor-slope predictor has no Linux
  analogue unless zswap or zram is configured.
  The Linux predictive signal is PSI `some` rising plus `MemAvailable` slope; the
  degradation signal is PSI `full` and swap-in rate from `/proc/vmstat` (`pswpin`), not
  swap used. Say so, or the Linux provider will be asked to emulate a signal that does
  not exist.
- **The kernel OOM killer exists, and it is not on our side.** Unlike macOS, Linux has a
  safety net, and it may kill the orchestrator, the broker, or an unrelated process.
  Raising `oom_score_adj` on agent leaves is unprivileged and steers the kernel toward
  the right victim; lowering it on the broker needs `CAP_SYS_RESOURCE`. The sentinel
  must also recognise an OOM kill (exit by `SIGKILL` plus `memory.events` `oom_kill`) as
  a host-pressure preemption rather than an adapter failure.
  The plans do not mention `oom_score_adj`.
- **`pidfd` solves PID reuse for owned children.** `pidfd_open` (5.3+) gives an identity
  that cannot be recycled and a pollable exit, and `pidfd_send_signal` signals that
  identity. This is strictly better than PID plus start time for owned mode and
  integrates with `asyncio` directly.
  Not mentioned.
- **`cgroup.kill` and the subreaper make Linux containment stronger than macOS.**
  `cgroup.kill` (5.14+) terminates a whole cgroup atomically, removing the enumeration
  race that deepest-first walks exist to mitigate.
  `PR_SET_CHILD_SUBREAPER` on the launch wrapper makes orphaned grandchildren reparent
  to the wrapper instead of `init`, so the tree stays findable even without cgroups.
  `clone3(CLONE_INTO_CGROUP)` is not reachable from Python, so the wrapper writes its
  own PID to `cgroup.procs` before `exec`, which the wrapper design already permits.
  On systemd hosts, `systemd-run --user --scope` provides an unprivileged delegated
  cgroup. The plan’s “optional delegated cgroup v2” bullets should name these.
- **PSS is not free.** `smaps_rollup` walks page tables; on a multi-gigabyte process a
  read costs tens of milliseconds, and the tree is sampled every interval.
  cgroup `memory.current` is constant time.
  The cost model that makes macOS sampling “nearly free” does not carry over, so the
  Linux provider needs a sampling-cost budget and an accuracy gate like the guard’s
  `needs_accuracy`.
- **Clocks.** `CLOCK_MONOTONIC` stops during suspend on Linux; `CLOCK_BOOTTIME` does
  not. The plan mentions boot time once, in a test bullet.
  Put it in the clock-domain design beside `mach_continuous_time`.
- **No Linux calibration.** Reserve fraction, PSI thresholds, and settle windows have no
  Linux measurement behind them.
  Add a bead for a dedicated-host Linux soak before Linux defaults ship, parallel to the
  macOS gated live tests.

None of this changes the architecture.
It changes whether “macOS and Linux as tested, first-class platforms” is a plan or an
aspiration.

### F5. Windows is declined, and the goal says otherwise

Both plans list Windows as a non-goal ("Add Windows support without a reliable telemetry
and process-containment design"; “Promise Windows support without a platform-specific
identity, measurement, and containment design”). That is a defensible first-release
scope, and it is also a decision that should be made explicitly rather than inherited.
If Windows is wanted, the capability-matrix design already accommodates it, and the
platform primitives are better than macOS in one respect: Windows has a real containment
object.

What a Windows provider needs, so the decision can be made on facts:

| Capability | Windows primitive | Note |
| --- | --- | --- |
| Owned containment | Job Objects with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`; `TerminateJobObject` | The cgroup analogue. Nested jobs since Windows 8 |
| Atomic placement | `CreateProcess` with `CREATE_SUSPENDED`, `AssignProcessToJobObject`, then `ResumeThread`; or `PROC_THREAD_ATTRIBUTE_JOB_LIST` | No wrapper needed; the child cannot run before it is in the job |
| Tree accounting | `QueryInformationJobObject` (`JobObjectExtendedLimitInformation`, peak job memory) | Exact, no enumeration race |
| Hard limit | `JOB_OBJECT_LIMIT_JOB_MEMORY` (commit) | Kills on breach like `memory.max`; same caveat about healthy transients |
| Identity | PID plus `GetProcessTimes` creation time | Same shape as macOS and Linux |
| Host budget | `GlobalMemoryStatusEx` (`ullAvailPhys`, commit limit and charge) | Commit charge is the pagefile-backed analogue of swap |
| Process cost | `GetProcessMemoryInfo` `PrivateUsage`, not `WorkingSetSize` | Working set is RSS with the same double-counting |
| Degradation | Commit charge approaching the commit limit; pagefile growth | No PSI equivalent |
| Pause | None safe. `NtSuspendProcess` is undocumented | Design the Windows policy without producer pauses |
| Exit observation | `WaitForSingleObject` on the process handle, or job completion port | `asyncio` Proactor supports handle waits |
| Python launch | `subprocess` uses `CreateProcess`; `ctypes` to `kernel32` for jobs | No `posix_spawn` question |

**Fix.** Add one paragraph to the system plan that records the decision: either Windows
is a later platform with the table above as its starting capability record and a bead,
or Windows is a permanent non-goal for the safety package and Metaproc’s Windows story
stays “unsupported”.
Either is acceptable.
Leaving it as an inherited non-goal is not, given the stated objective.

### F6. Documentation structure

- **Two owners of the gauge facts.** `docs/memory-accounting-reference.md` already
  exists on `main` with the XNU and Linux citations, the 2.1× gauge measurement, and the
  RSS-in-both-directions argument.
  The new research record restates the same facts and never links to it.
  Make the research record the owner of the *control model* and link to the reference
  for gauge semantics, or fold the reference into the research record and leave a
  pointer. Two documents that each claim to own the same measurement will drift.
- **Length.** The system plan is 1,363 lines.
  The “Why the Other Approaches Are Insufficient” table and the Procguard lessons table
  are research, not plan; moving them to the accounting-and-control record shortens the
  plan and gives the research record its evidence.
- **Header convention.** Completed specs use `author`, `category`, and `tracking_bead`
  in front matter and a status quote block under the title.
  The two plans use `author: Metaproc team` and no bead field.
  Minor, but the bead field is how a reader finds the graph.
- **Shipped-doc consistency.** `src/metaproc/docs/arch-runpool.md` still says “Future
  Linux work should add cgroup-aware readings” while the backlog file now points at the
  plan. That is fine for a docs-only PR; note it for the rollout step that updates
  shipped documents.

### F7. Smaller points

- The system plan’s `metaproc pool host-admission`, `pool events`, and `pool health`
  views are new public CLI surface.
  They are appropriate, but `AGENTS.md` requires a migration note for public CLI
  changes; a sentence in “API and Artifact Changes” would cover it.
- Unix-domain socket paths are limited to 104 bytes on macOS. The broker state root must
  be short or the socket must live under a short symlinked path; a state root under a
  user’s Application Support directory will hit this.
- The system plan’s current-behavior table says today’s sampling “does not consume the
  kernel VM-pressure state used by the successful guard experiment”.
  Metaproc reads `kern.memorystatus_level` (a percentage); the guard reads
  `kern.memorystatus_vm_pressure_level` (values 1, 2, 4). The plan should name the
  second sysctl so the provider is not written against the first one.
- The research record’s Gemini finding is correct and important; the recommended
  follow-up (make the adapter honor or reject `no_session_persistence`) is small,
  independent of Safeproc, and worth its own bead now.
- The package plan’s `Private :: Do Not Upload` classifier and prefixed tags are good.
  Add that `uv build --package safeproc --no-sources` must run in CI on every PR that
  touches `packages/`, not only at handoff, or the workspace leak gate will only catch
  problems at release time.

## Platform Assessment

The question asked was whether there are clear plans for each platform.
The short answer: macOS yes, Linux partially, Windows no by decision.

| Capability | macOS | Linux | Windows |
| --- | --- | --- | --- |
| Host budget | Specified with evidence: free + inactive + purgeable via `host_statistics64` | Specified: `MemAvailable`. Missing: cgroup-limited hosts | Not planned; `GlobalMemoryStatusEx` |
| Degradation alarm | Specified with evidence: `kern.memorystatus_vm_pressure_level` 1/2/4, compressor slope, red-line ratio | Specified: PSI. Missing: availability and privilege gating, no compressor analogue, swap-in rate | Not planned; commit charge |
| Swap and disk coupling | Measured in the guard; contradicted by one non-goal in the plan | Not applicable in the same form; swap-in rate instead | Not planned |
| Process cost | Specified with evidence: tree `phys_footprint` via `proc_pid_rusage` | Specified: PSS or `memory.current`. Missing: sampling-cost model | Not planned; `PrivateUsage` |
| Identity | PID + create time | PID + `/proc/<pid>/stat` start time. Missing: `pidfd` | Not planned; PID + creation time |
| Owned containment | Session + process group; no kernel container | Session + group; cgroup v2 when delegated. Missing: `cgroup.kill`, subreaper, systemd scope path | Not planned; Job Objects are stronger than both |
| Monitored tree | Identity-fenced walk, deepest-first, `SIGSTOP` before enumerate (in guard, partly in plan) | Same walk. Missing: subreaper note | Not planned; no pause primitive |
| Producer pause | In guard with duty cycle; plan lacks the cap | Same semantics; plan lacks the cap | Not possible safely |
| Kernel safety net | None (no `CONFIG_JETSAM`) | OOM killer. Missing: `oom_score_adj` and OOM-as-preemption | Commit-limit failures; job memory limit |
| Sleep-aware clock | `mach_continuous_time` specified | `CLOCK_BOOTTIME` mentioned once | Not planned |
| Nonforking launch | Required; `os.posix_spawn` path not designed (F2) | Required; `vfork` path partially mitigates; `posix_spawn` path not designed | Not applicable |
| Failure corpus and calibration | Nineteen journals, 96 guard tests, measured thresholds | None | None |

## Recommended Changes

Before merging pull request 62, in the documents themselves:

1. Resolve F1: one owner per topic, one phase list, duplicated tables removed.
2. Add the F3a, F3b, F3c, F3d, F3g, and F3h rules to the system plan’s Design and Safety
   Invariants sections.
   Reword the disk non-goal.
3. Add the F3e sentinel self-health paragraph and the F3f zombie case.
4. Expand the Linux provider section with the F4 gating, budget, OOM, `pidfd`,
   `cgroup.kill`, subreaper, and clock points, and add the Linux calibration bead.
5. Record the Windows decision (F5), one paragraph either way.
6. Link the research record to `docs/memory-accounting-reference.md` and decide which
   owns gauge semantics (F6).
7. Add the F2 “Launch primitive” section to the package plan, even if it only states the
   constraints and defers the mechanism to the spike.

Before implementation begins, as beads under `mp-bd6v`:

1. Spike: `os.posix_spawn` with `setsid`, plus a stdlib exit waiter (`pidfd` on Linux,
   `kqueue` on macOS) under `asyncio`; blocks `mp-3c0g`.
2. Linux calibration soak on a dedicated host; blocks the Linux defaults in `mp-c225`.
3. Gemini adapter: honor or reject `no_session_persistence`; independent of Safeproc.
4. If Windows is a go: a Windows capability record and provider bead after `mp-3i22`.

Nothing here argues against the architecture.
The plans are unusually well grounded in evidence for macOS; the requests above bring
the rest of the plan to the same standard.

## Appendix: Provenance

Reference checkouts used for this review, placed under the ignored `attic/reference/`
directory and not committed:

```sh
git clone https://gist.github.com/5b43e0d44166b9c7fe8157ee938cb0d5.git attic/reference/memory-guard-gist
git -C attic/reference/memory-guard-gist checkout d5e67ea86b37fc14672677249fbf93af2222581c
git clone --branch v1.5.1 https://github.com/denispol/procguard.git attic/reference/procguard-v1.5.1
```

Procguard was read statically and neither built nor executed, matching the PR’s own
verification note. The gist is the author’s own code; Procguard is MIT-licensed and its
`LICENSE` file is retained in the checkout.
Neither is proposed for vendoring; the package plan’s rule that Procguard behaviors are
translated into implementation-independent contract tests with provenance is the right
one.

PR facts were read through the GitHub API: head
`5f639e179dd7189ec7796aca2d0a79e10618b8cf`, base
`10f51859c6b09ca41cddb9384c7ee0f549de984f`, CI run 33583159906 with five successful jobs
(lint, distribution, Python 3.12, 3.13, 3.14).

## Status Addendum (2026-09-02)

Addressed on the pull request branch in `1333fd5`, merged with the concurrent upstream
research commit in `3be422d`. Tracking: parent bead `mp-yajq` with one child per
finding; follow-up beads `mp-t9u5` (launch-primitive spike, blocks `mp-3c0g`), `mp-sfc0`
(Linux calibration soak, blocks `mp-c225`), `mp-4ksz` (Windows capability record, after
`mp-3i22`), and `mp-v0ka` (Gemini `no_session_persistence`).

- F1: fixed. Both plans open with a document-ownership statement; the system plan drops
  its duplicated layer, Python-surface, and alternatives tables and links to their
  owners; the package plan’s phase numbering is canonical and the system plan’s rollout
  phases are named integration stages.
- F2: fixed. The package plan has a Launch Primitive section covering the `posix_spawn`
  call, the exit waiter, descriptor hygiene, and the wrapper handshake; the system
  plan’s owned-launch section points to it; spike `mp-t9u5` blocks the broker work.
- F3a: fixed. Invariant 24, the sentinel’s duty-cycle bullets with the guard defaults,
  and a producer-pause test group.
- F3b: fixed. Invariant 25 and the sentinel bullet.
- F3c: fixed. Invariant 26 and the `critical` exit rule under the state machine.
- F3d: fixed. The disk non-goal is reworded; the `critical` row names the swap-volume
  suspension distance and the red-line ratio; the macOS provider records them.
- F3e: fixed. A sentinel self-health paragraph, the macOS provider bullet, and a test.
- F3f: fixed. Invariant 28, the shedding text, the accounting tests, and the package
  corpus.
- F3g: fixed. A termination-mechanics paragraph under shedding and a test group.
- F3h: fixed. Invariant 27 and the `catastrophic` row and exit-rule text.
- F3i: fixed. A supply-chain bullet specifies the sanitizing export, its location, and
  its relation to the hygiene rule; Phase 1 references it.
- F4: fixed in the documents; calibration deferred.
  The Linux provider is rewritten as requirements, the capability table gains Linux
  rows, the research record gains Linux findings, and tests cover each item; Linux
  defaults wait on `mp-sfc0`.
- F5: fixed. Windows is recorded as deferred, not declined, with a starting capability
  record; `mp-4ksz` writes the provider design after the macOS and Linux providers.
- F6: fixed. The research record owns the control model and links to
  `docs/memory-accounting-reference.md` for gauge citations; the Procguard and
  alternatives tables moved there with verified source locations; both plans carry
  `author`, `category`, and `tracking_bead`; rollout step 8 names `arch-runpool.md`.
- F7: fixed. CLI migration note, socket path limit, exact pressure sysctl, and the CI
  build gate on `packages/` pull requests are in the plans; the Gemini setting is
  tracked as `mp-v0ka`.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
