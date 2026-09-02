---
title: RunPool Host Safety Envelope
description: Design owned launch and existing-process monitoring, ship them without a standalone pool, and defer any RunPool extraction decision.
date: 2026-09-01
status: Draft
---
# Feature: RunPool Host Safety Envelope

**Date:** 2026-09-01

**Author:** Metaproc team

**Status:** Draft

## Overview

Metaproc should protect a local host even when a consumer starts several independent
`run-process` commands, an adapter has a large startup transient, or a bug prevents one
RunPool from reacting normally.

The existing architecture has the right ownership boundaries: every local agent launch
belongs to Metaproc, RunPool supervises complete process groups, and a disk-backed host
gate coordinates otherwise independent runs.
The missing part is the resource model.
Current admission counts live processes and estimates every process from one
steady-state RSS number.
It does not represent a process that consumes several gigabytes for its first minute and
then settles below one gigabyte, and it does not space starts across separate Metaproc
parents. Four count-admitted starts can therefore overlap four large allocation spikes
before the non-preemptive pressure controller has anything to drain.

The recommended design has four layers:

1. **One admitted launch path.** Every local agent attempt, in every execution shape,
   must obtain a host claim.
   Failure to obtain a claim blocks or fails the attempt; it never becomes permission to
   launch without one.
2. **Startup-aware host admission.** A profile declares its startup peak, startup
   window, steady cost, and optional launch spacing.
   One host authority serializes decisions and accounts for overlapping startup
   reservations across all local Metaproc runs.
3. **Measured pressure response.** Fast platform telemetry closes admission before a
   burst grows, and one host-elected controller may shed restartable work under
   sustained, measured critical pressure.
   Predictive signals may pause starts but may not kill work.
4. **Independent containment.** A small host sentinel runs outside every RunPool event
   loop. It should normally do nothing.
   If Metaproc’s in-process controller stops making progress while the host approaches
   failure, the sentinel can freeze launch producers and terminate only identity-fenced
   registered process groups.

These layers should sit behind a publishable component boundary.
The strongest candidate is a **standalone process-safety runtime** with one shared core,
two first-class supervision modes, a per-user broker/sentinel, and thin command-line
interfaces. Owned mode admits and contains a process tree before the target executes.
Monitoring mode observes an existing tree and may apply explicitly authorized guard
actions without claiming retroactive ownership or an operating-system attachment.
The two modes share host evidence, pressure policy, identity records, journals, and
platform backends; they differ in lifecycle authority and containment guarantees.

Owned mode is not merely a monitor started at approximately the same time as the target.
Its guarantee begins before the memory-heavy command executes: admission is granted, an
isolated process group is created, and identity and cleanup ownership are established
before control passes to the target.
Monitoring mode remains useful precisely because it requires none of that integration.
It can protect or profile builds, test suites, agent CLIs, and existing orchestration
that the runtime did not launch.

The runtime is more capable than a process-tree memory monitor and narrower than
Metaproc itself. The stable abstraction is safety for one process tree, whether owned
from launch or monitored later.
A command pool is a possible later layer above the owned boundary, not part of the first
package release.

The public
[memory guard](https://gist.github.com/jlevy/5b43e0d44166b9c7fe8157ee938cb0d5) supplies
the first macOS backend, decision policy, journal, and failure corpus.
Its process-tree monitoring interface should remain a first-class product mode that
works without a broker, daemon, or Metaproc installation.
Owned launch remains the stronger path because it can admit work before spawn and prove
process-group ownership.

Metaproc should build on the owned-process and host-authority layers.
The first package release should not contain `SafeRunPool`, a submission queue, or a
`pool` CLI. Metaproc retains its in-memory queue and adaptive controller while using the
standalone `SafeProcess` boundary.
Only after that package and integration seam have stabilized should a separate spike
evaluate whether extracting the pool would reduce duplication without imposing a second
queue or lockstep releases.

The [Safeproc Local Incubation](plan-2026-09-01-safeproc-local-incubation.md) plan owns
the package layout, uv workspace, quality gates, versioning, and history-preserving
extraction mechanics.
This plan remains authoritative for host policy and Metaproc rollout.

## Goals

- Keep macOS and Linux hosts responsive when one or several local Metaproc runs launch
  memory-heavy agent CLIs.
- Make launch simultaneity a first-class resource constraint instead of treating active
  process count as a proxy for it.
- Enforce memory truth at host scope across scalar steps, fan-outs, mapped scopes,
  independent runs, and mixed execution profiles.
- Preserve high steady-state throughput by pacing expensive starts instead of imposing a
  permanently low concurrency cap.
- Prevent a RunPool bug, blocked event loop, or telemetry failure from removing every
  safety layer.
- Make the host-safety substrate reusable by non-Metaproc launchers without requiring
  them to adopt Metaproc’s process model.
- Expose owned launch and existing-process monitoring as separate, typed modes over one
  safety core, with their different authority and guarantees visible in every request,
  event, and result.
- Preserve a small brokerless `ProcessMonitor` and `watch` guard for arbitrary existing
  process trees without requiring the broker, background service, or Metaproc.
- Establish an owned-process abstraction that the standalone `run` CLI and Metaproc can
  both build on.
- Ship the reusable process-safety package before evaluating pool extraction.
  The first package has no submission queue or `pool` CLI, and Metaproc retains one
  subprocess scheduler.
- Keep ordinary Metaproc work on DAGs, lanes, adapters, retries, artifacts, and provider
  policy independent of standalone-runtime releases.
- Keep every admission, wait, pressure transition, preemption, and sentinel action
  attributable through supported status and event surfaces.
- Preserve process-group ownership, retry semantics, resume safety, and compatibility
  with released execution profiles and runtime artifacts.

## Non-Goals

- Automatically discover or control arbitrary processes that no user explicitly launched
  through the runtime or selected for monitoring with `watch`. The host controller may
  identify outside pressure, but it must not kill unrelated applications.
- Turn a general disk-space warning into a memory kill trigger.
  Swap headroom is relevant on macOS; ordinary low disk remains a separate operational
  problem.
- Make a fixed low `--max-concurrency` the default safety mechanism.
- Promise that an adapter has one universal memory cost.
  CLI version, model, platform, prompt, tools, adapter-local state, and configuration
  can all change its shape.
  A working directory matters when the client maps it to a project-scoped state bucket;
  it is not itself a causal memory variable.
- Add Windows support without a reliable telemetry and process-containment design.
- Publish Metaproc’s task scheduler, retry engine, execution lanes, adapters, or run
  artifacts as part of the host-safety library.
- Include a standalone pool, submission queue, or `pool` CLI in the first package
  release.
- Build a durable job server, workflow scheduler, retry database, or distributed queue
  as part of the process-safety package.
- Require `watch` mode to start or contact a broker, run a daemon, or import Metaproc.
- Present a monitored target as safely owned merely because its monitor started near the
  same time. Pre-execution admission, isolated process-group creation, identity
  registration, and cleanup ownership are required for the owned guarantee.
- Move Metaproc scheduling code across the package boundary merely to reduce line count.
  Extraction must improve ownership, testing, and failure isolation.
- Replace cloud worker limits.
  Containers and GCP Batch have separate placement and cgroup contracts, although they
  should reuse the resource-claim model where practical.

## Background

### What Metaproc Already Does Well

RunPool already provides adaptive per-pool capacity, process-tree health monitoring,
isolated process groups, bounded termination with `SIGKILL` escalation, durable events,
provider-pressure control, and disk-backed host slots.
`run-process` now shares one RunPool across its local agent leaves, and scalar launches
also enter host admission.

These pieces solve lifecycle ownership and many ordinary concurrency failures.
They do not yet form a complete host safety boundary:

| Current behavior | Remaining failure |
| --- | --- |
| Startup capacity is `available memory × budget fraction / estimated_process_rss` | The estimate is one scalar and can describe settled memory while understating a startup peak by an order of magnitude. |
| Host admission leases one count slot per active process | Several admitted starts can allocate their peaks simultaneously, and callers using different limits do not share one coherent byte budget. |
| Scalar admission waits for a slot | After its timeout or an admission I/O error it currently launches without a slot. A failed safety mechanism therefore increases load. |
| High and critical pressure lower future semaphore capacity | Reduction is non-preemptive. Long-running active agents can wedge the host before one exits. |
| macOS budgets from `vm_stat` reclaimable pages | Sampling forks `vm_stat` and `sysctl` subprocesses and does not consume the kernel VM-pressure state used by the successful guard experiment. |
| Per-process health sums RSS across a process tree | macOS RSS excludes compressed pages and double-counts shared pages across processes, so it is unreliable for attribution or victim sizing under pressure. |
| Each RunPool monitors itself | A blocked or buggy pool has no independent failure boundary. |

The public memory-guard experiment records the motivating workload shape.
On one macOS host, a Gemini CLI process reached roughly 3.6–4.6 GB during a startup
window lasting up to about 71 seconds, then settled between tens of megabytes and
roughly 1.2 GB. Starting eight together can therefore require about 32 GB while spacing
the same eight lets the host sustain the same steady-state concurrency.
The same experiment found that host-wide reclaimable memory and kernel pressure
distinguish dangerous states more reliably than summed RSS, and that a watchdog which
forks to sample can itself become starved under severe pressure.

Follow-up controls isolated the largest observed Gemini CLI 0.40.1 startup transient.
The same short prompt peaked at 0.25 GB with clean project state and 5.15 GB after a 3.4
GB project-history bucket was copied into otherwise clean state.
Disabling Gemini’s session-retention cleanup against the copied state reduced the peak
to 0.26 GB. Official source shows that startup cleanup enumerates the current project’s
session files and concurrently parses every JSONL record, including in metadata-only
mode.
The apparent working-directory effect came from selecting a fresh state bucket, not
from avoiding a repository scan.
The
[agent CLI startup-memory research](../../research/research-2026-09-01-agent-cli-memory-usage.md)
preserves the measurements and source path; the
[host memory-accounting research](../../research/research-2026-09-01-host-memory-accounting-and-control.md)
preserves the platform gauge semantics.

This causal mitigation should reduce demand for supported Gemini versions, but it does
not replace host admission.
Client behavior, state layout, or defaults may change, and other clients still have
startup transients of their own.

### Lessons From Procguard

A static source review of
[Procguard v1.5.1](https://github.com/denispol/procguard/tree/v1.5.1) provides a useful
implementation and test reference for the owned-process layer.
Procguard is a small macOS supervisor for one command, not a host-wide admission
controller or concurrent pool.
Its strongest choices are an atomic `posix_spawn` launch into a new process group,
`kqueue` exit and timer events, separate continuous and active clocks,
physical-footprint sampling through `proc_pid_rusage`, terminal usage from `wait4`, a
versioned JSON result, and explicit tests for fast-exit and signalling races.

The review also exposes boundaries that the standalone runtime must handle differently:

| Procguard behavior | Lesson for the standalone runtime |
| --- | --- |
| The ordinary path uses `posix_spawn`, but enabling a resource limit switches to `fork`. On macOS the attempted `RLIMIT_AS` memory limit is rejected, so memory enforcement still comes from a later 100 ms poll. | Safety options must not make launch less safe under memory pressure. Keep the supervising process on a nonforking path; if a limit must be applied before the target starts, spawn a minimal wrapper that applies it and then calls `exec`. |
| Memory and CPU polling read only the root PID, even though timeout signals normally target its process group. | Every metric must declare `root`, `tree`, `cgroup`, or `host` scope. Root physical footprint is useful evidence but cannot be presented as process-group memory or used alone for group victim sizing. |
| Wall time uses `mach_continuous_time`, while active time excludes system sleep. | Every lease, grace period, startup window, and timeout needs an explicit clock domain. A startup reservation must not expire merely because the Mac slept while the target could not finish starting. |
| A v1.5.1 regression fix was needed because a zero wall timeout bypassed memory, throttle, heartbeat, and stdin monitors. | Governors compose independently. Disabling or zeroing one deadline must never bypass another configured monitor or the host authority. |
| Signal forwarding is a process-global self-pipe. Its cleanup contract forbids concurrent runs and resets handlers rather than restoring an application-owned signal configuration. | `SafeProcess` must support many concurrent instances and must not take implicit per-run ownership of process-global signals. Use one application-level signal router or an explicit caller-owned forwarding policy. |
| The raw child handle has no terminal cleanup guard, so an internal monitor error can return without a demonstrated child-reap or ownership-transfer path. | Every post-spawn exit path must either reap and clean the group or atomically transfer it to the broker or sentinel before returning. Supervisor failure cannot orphan the workload it was meant to contain. |
| The CLI and Rust library share one runner, and results are nonexhaustive and schema-versioned. The public configuration remains experimental and has accumulated timeout hooks, retries, file waiting, heartbeat, and throttling. | Keep one implementation behind the CLI and Python surfaces, use additive typed contracts, and resist moving orchestration conveniences into the safety boundary. Shell hooks, retry policy, and dependency waiting remain above it. |
| The test suite stresses immediate exits, `ESRCH` registration races, process-group signalling, zero-duration combinations, `SIGSTOP` cleanup, parsers, and timing arithmetic. Its Kani scope excludes the runner loop, FFI, and signal handler, and several proofs model simplified state rather than the implementation itself. | Reuse the failure corpus and layered test methods. State formal or model-checking coverage narrowly and require proofs to exercise shared production functions where possible. |

Procguard is therefore a reference, not a dependency candidate or replacement for the
proposed runtime. It validates the small native-supervisor shape and the CLI-and-library
seam, while its macOS-only, root-process, single-command contract reinforces the need
for separate host admission, cross-client identity, tree accounting, and Linux backends.

### Safety Principles

The design follows six rules.

1. **Admission happens at the scope of the resource.** Memory is a host fact, so no
   per-run controller can be its sole authority.
2. **A process has a memory shape, not one cost.** Admission must distinguish an
   outstanding startup allocation from an already materialized steady working set.
3. **Projection and intervention require different evidence.** A slope or predicted
   time-to-floor may stop new work.
   Only a measured critical state may take work away.
4. **A safety failure fails closed.** A corrupt lease namespace, timeout, or unavailable
   required gauge cannot authorize an otherwise prohibited launch.
5. **Observation is not ownership.** Owned and monitored-process modes may reach the
   same host diagnosis from the same evidence, but only pre-execution control can
   establish an authoritative process group and admission guarantee.
6. **The final guard is independent.** An in-process assertion cannot protect against
   the process containing that assertion hanging or spawning incorrectly.

## Design

### Safety Invariants

The implementation must make these properties testable:

1. Every local agent subprocess has exactly one active host claim before spawn.
2. The claim remains held until the complete owned process group exits.
3. No timeout or expected I/O error converts a missing claim into an ungoverned launch.
4. Host admission decisions are serialized across processes, and every decision uses a
   fresh or explicitly bounded-age host sample.
5. A newly admitted startup cannot consume headroom already reserved for another start.
6. Predictive signals never kill active work.
7. At most one controller performs a shedding round for one host-pressure episode.
8. An owned pressure kill targets only a recorded process identity and its isolated
   process group. A monitored-process intervention targets only identity-fenced
   enumerated descendants and never assumes that an inherited process group is safe to
   signal.
9. A host-pressure preemption is durable, distinguishable from an adapter failure, and
   resumable without being misclassified as a deterministic workload error.
10. The independent sentinel can act when all RunPool event loops are unresponsive, and
    it cannot act on unrelated process trees.
11. Broker state and journals contain no credential-bearing environment values, prompts,
    or unredacted command arguments.
12. An absent or protocol-incompatible standalone runtime cannot silently downgrade an
    authoritative launch to unsupervised execution.
13. Monitored `watch` mode remains usable with the broker disabled or unavailable and
    without importing the owned-process or broker layers.
    Observation and journaling are its default authority; signalling requires explicit
    operator policy.
14. At every phase, Metaproc has one queue and one adaptive-capacity controller; no
    migration becomes a permanent double-scheduling layer.
15. Every deadline and persisted timestamp declares its clock semantics; system sleep
    alone cannot age a live process out of its startup reservation or identity claim.
16. Disabling one timeout or governor does not disable any other configured monitor,
    claim, heartbeat, or containment rule.
17. Concurrent `SafeProcess` instances share no mutable per-run global signal state, and
    the library never replaces application signal handlers without an explicit contract.
18. Every post-spawn error path either performs bounded process-group cleanup or proves
    that the broker or sentinel accepted ownership before the caller receives the error.
19. Every resource observation declares whether it represents one PID, an owned tree, a
    cgroup, or the host; lower-scope evidence is never silently promoted to a wider
    scope.
20. Every target record declares an immutable `owned` or `monitored` mode.
    No boolean, fallback, reconnect, or pattern match silently upgrades a monitored
    target to owned.
21. Owned authority is established before the target command executes.
    There is no interval in which an admitted target can allocate while its
    process-group identity or cleanup owner is unknown.
22. A monitored target and every descendant selected for intervention are fenced by PID
    plus create time immediately before signalling.
    An argv pattern may locate a candidate for observation, but it never authorizes a
    signal or substitutes for revalidating tree membership.
23. Equivalent normalized host samples produce the same pressure classification in owned
    and monitored-process modes.
    Mode changes the actions that are safe and the guarantees that can be stated, not
    the interpretation of host evidence.

### One Host Resource Authority

Replace the count-only slot namespace with a versioned claim namespace owned by one
elected per-user broker.
Its state root must be host-local, private to the effective user, and neutral rather
than living permanently under `.metaproc`:

```text
<safeproc-state>/<host-instance>/local-agents/
  broker-lock/lease.json
  broker.sock
  host-state.json
  claims/<claim-id>/lease.json
  waiters/<waiter-id>/request.json
  incidents/<incident-id>.json
  journal.jsonl
```

Atomic `mkdir` elects `broker-lock/`; the lease uses PID plus process create time so a
replacement can reject PID reuse.
The broker serializes admission decisions in one event loop and publishes every state
transition atomically.
Clients communicate through a protected local socket and never mutate live claim files.
If the broker dies, existing subprocesses continue under their recorded claims, new
starts fail closed, and a replacement reconstructs live ownership before reopening
admission.

Waiting requests remain inspectable on disk, but the broker, not independent polling
clients, owns their ordering and wakeups.
Ordering uses request time plus aging so a wide early client cannot starve other clients
indefinitely. The current `.metaproc` count-slot namespace remains readable only for
migration and released-run inspection; it must never compete with the new broker for
authority.

The unprivileged boundary is one broker per effective user and host instance.
It measures whole-host pressure but controls only processes registered by that user.
On an ordinary single-user macOS workstation this is the intended host authority.
A truly machine-wide multiuser service would require installation, privilege, and trust
contracts that are outside the first release; Linux cgroup or system-service integration
may provide that later.

A claim records:

- claim, client, workload, attempt, resource-profile, and backend identities, plus
  optional redacted correlation keys supplied by the client
- owner PID and create time, child PID and create time, and process-group identity
- declared startup peak, startup window, steady cost, and launch spacing
- `reserved`, `startup`, `steady`, `terminating`, or `released` phase
- acquisition, scheduled-launch, actual-launch, phase-transition, and heartbeat times
- observed current and peak attributable memory when the platform can supply it
- restartability, scheduling priority, and prior host-pressure preemption count

The namespace is a resource authority, not a new scheduler.
RunPool still chooses which ready task asks next; the host authority says whether that
attempt may start now.

### A Phase-Aware Memory Claim

Add a typed memory shape to `ExecutionProfileResources` in a backward-compatible schema
revision:

```yaml
resources:
  memory:
    steady_mb: 750
    startup_peak_mb: 4600
    startup_window_s: 75
    launch_spacing_s: 30
    platform_overrides:
      linux:
        startup_peak_mb: 1200
        startup_window_s: 20
```

The names describe scheduler accounting, not a promise that RSS is the platform’s exact
metric. On macOS, observations should use physical footprint where available; on Linux,
proportional set size or cgroup accounting is preferable.

Compatibility rules:

- Existing `estimated_process_rss_mb` or `estimated_process_rss_bytes` remains accepted.
  When no memory shape exists, it maps to both startup and steady cost with a zero
  startup window and no explicit spacing.
- `host_max_concurrency` remains an optional hard count ceiling.
  It is a final count cap, not the memory model, and host admission v2 must define
  coherent behavior when active profiles declare different values.
- `METAPROC_HOST_MAX_LOCAL_AGENTS` remains the operator’s host-wide emergency count cap.
- New status artifacts record both the authored values and the effective platform values
  selected at runtime.

Tested built-in profiles should carry measured shapes.
The first Gemini correction should distinguish clean or retention-disabled state from
retention-enabled accumulated project state.
Controlled Gemini CLI 0.40.1 measurements put the former near 0.25-0.26 GB and the
latter at 5.15 GB; production-shaped observations reached 4.6 GB for as long as 71
seconds. Until the adapter can prove that startup cleanup is disabled or state is
isolated and bounded, it must use a conservative high-spike Darwin profile rounded
upward. A supported headless adapter should prevent the known scan when compatible, then
remeasure the exact supported version and state regime.
A V8 heap cap is not a lower memory profile: the controlled cap converted the spike into
an out-of-memory crash after a multi-gigabyte allocation.
A global 500 MB startup assumption must not remain the tested Gemini default.

An unknown or materially changed profile enters **cold-start calibration**: the host
serializes its first start, observes its peak through a bounded window, and uses the
observed high-water mark with a safety margin for the rest of that host session.
Runtime learning is visible and scoped to the exact adapter version, platform, model,
and relevant adapter-state and configuration fingerprint.
Record the working directory as diagnostic context when it selects client state, but do
not use a raw path as the causal profile identity.
It is never silently promoted into a durable cross-version default.

### Admission and Launch Pacing

Every admission decision uses current reclaimable headroom and reservations for starts
whose allocation has not yet fully appeared in that measurement.

Conceptually:

```text
reserve = max(host_min_reserve, total_memory × host_reserve_fraction)
projected_headroom = current_reclaimable
                     - outstanding_startup_reservations
                     - requested_startup_peak

admit only when projected_headroom >= reserve
```

An admitted claim conservatively reserves its startup peak before spawn.
The reservation expires only after the declared startup window and healthy observation,
or earlier when reliable attributable telemetry proves the process has settled.
If the owner hangs, the reservation remains conservative until identity-based stale
reclamation proves the owner and child are gone.

The host authority also enforces the selected profile’s `launch_spacing_s` against the
last compatible host launch.
This pacing is host-wide: two `run-process` parents, two RunPools, and two same-level
steps cannot each start their own independent stagger.
A fixed delay is therefore a supported profile hint, not a downstream wrapper
environment variable.

Fresh host measurement already reflects established active processes and unrelated
applications. Reservations cover the race window between admission and materialized
allocation; they must not become a second permanent subtraction of every active
process’s steady memory.
The implementation should start conservatively and release reservations based on
validated observations only after replay tests show that doing so does not admit
overlapping peaks.

The current per-pool memory ceiling may remain as a local throughput controller during
migration, but it is no longer the host safety authority.
The host gate is authoritative for every local launch; provider and operator ceilings
remain at their existing scopes.

### Host Pressure State Machine

Normalize platform readings into explicit host states and keep measured and predictive
evidence separate:

| State | Evidence | Admission | Active work |
| --- | --- | --- | --- |
| `healthy` | Headroom above reserve, no active stall or dangerous growth | Admit by claims and pacing; local pools may ramp | Continue |
| `watch` | Stable moderate pressure or a single predictive warning | Do not ramp; admit only if the full startup projection remains safe | Continue |
| `embargo` | Predicted floor crossing, fast compressor or swap growth, or platform warning requiring confirmation | Freeze new starts host-wide | Continue and sample faster |
| `critical` | Sustained measured critical pressure, unsafe reclaimable headroom, or Linux full-stall evidence | Freeze starts; broker opens one incident | Shed only after confirmation and fault attribution |
| `catastrophic` | Critical state is worsening, no cooperative response is observed, and the host is near the calibrated failure boundary | Freeze producers | Broker/sentinel may terminate owned groups |

Predictive evidence includes reclaimable-memory slope, compressor growth, and projected
time to a reserve. It may move the host into `embargo`; it cannot authorize shedding.
The guard experiment produced predictive warnings during healthy runs, which makes this
separation a correctness requirement rather than a tuning preference.

Pressure transitions use wall-clock confirmation rather than a fixed number of samples,
because sampling cadence itself can degrade.
Every sample records actual interval and lag.
Lag is diagnostic and may make the controller distrust a stale reading; it is never a
pressure trigger.

### Coordinated Pressure Shedding

Non-preemptive backoff remains correct for `watch` and most `embargo` states.
It is too slow once a host is in sustained critical pressure and active agents may run
for several minutes.

The elected broker is the sole responder.
It opens a durable incident record, rechecks host health and all live claims, determines
whether registered memory is large enough to change the outcome, and takes at most one
proportional round before a settle-and-remeasure interval.
Metaproc pools and other clients observe the episode and do not shed independently.

Victim selection should minimize lost work per expected byte recovered:

1. restartable attempts still in their startup phase
2. youngest low-priority attempts with the largest reliable attributable footprint
3. explicitly preemptible attempts
4. whole-workload abort only when the host is catastrophic and no narrower action can
   restore headroom

RSS alone cannot rank macOS victims.
Use physical footprint when available and combine it with claim phase, age,
restartability, and declared peak.
If most pressure is outside registered workloads, keep admission closed and report the
outside consumers; do not destroy registered work that cannot recover enough memory to
help.

A shed attempt receives a generic durable preemption event.
Metaproc projects it as `kill_reason: host_pressure_preempted` with a retry disposition
distinct from adapter retry.
It should become eligible again only after host recovery.
It does not count as a deterministic adapter failure, but a separate bounded preemption
budget prevents endless kill-and-restart loops.

### Independent Host Sentinel

The elected broker also serves as the independent sentinel; one small external process
is enough unless failure testing proves that admission and monitoring need separate
failure domains. The first safety client ensures that the broker exists before any
memory-heavy target launch; later clients join its namespace.
It exits after the namespace has had no live client or claim for a bounded idle period.
A stale broker lease is replaced using process identity, not PID alone.

The sentinel has a deliberately narrower job than RunPool:

- sample host pressure through nonforking platform primitives
- record cadence and host-state transitions in one host journal
- maintain the host launch embargo when in-process monitors are late or dead
- own incident pacing and verify that cooperative clients are making progress
- at the catastrophic boundary, stop registered producer PIDs before they can create
  more work, then terminate only registered, identity-fenced process groups
- always resume any producer it stopped during normal exit or failed intervention

It does not schedule tasks, parse run directories, retry work, select adapters, scan by
argv pattern, or kill unrelated processes.
It owns the host claim registry and incident state that the standalone CLI and supported
Metaproc commands expose.

The sentinel should avoid dependency resolution, shell commands, and subprocess-based
sampling in its hot path.
A separate Python entry point using `ctypes` and existing stdlib facilities is
acceptable for the first implementation; a native helper is not justified unless latency
and starvation tests show the Python process cannot meet the cadence contract.

### Standalone Process-Safety Boundary

Packaging is part of the failure boundary.
Moving policy into an imported library improves reuse but does not protect the host from
a blocked or corrupted Metaproc process.
The independent executable and the shared library therefore serve different purposes,
while using the same implementation.

The existing code supports a real seam, but not yet a foregone conclusion about the pool
itself. `runpool/README.md` already supports RunPool as a library for tools that need
adaptive local concurrency without process specs.
`runpool/backend.py`, `runpool/host_admission.py`, `runpool/monitor.py`,
`runpool/semaphore.py`, and `osutils/memory_pressure.py` contain generic lifecycle and
host logic. `runpool/pool.py` is more than 2,000 lines and mixes generic submission,
cancellation, and adaptive-capacity logic with Metaproc paths, execution lanes, status,
events, and provider policy.
The generic launch primitive also contains log-filter and invocation-sidecar behavior
that belongs in Metaproc hooks because it encodes Metaproc artifacts and credential
handling.

The assessment is therefore deliberately asymmetric:

| Candidate boundary | Assessment | Reason |
| --- | --- | --- |
| One repository for the safety core, owned `run`, monitored `watch`, broker, replay, and platform backends | Strong fit | These surfaces must agree on pressure evidence, process identity, intervention policy, and journal semantics. Splitting them invites safety-policy drift. |
| Both a CLI and a Python API over that core | Strong fit, if the CLI remains an adapter | Shell users need a side guard and launch wrapper; Metaproc needs typed in-process calls and events. This is one implementation with two entry points, not two products. |
| A finite standalone `pool` command | Possible later feature, outside the first package | It may broaden reuse, but it adds queue, ordering, cancellation, drain, and result contracts before the process boundary has operating evidence. |
| Moving Metaproc’s complete in-memory RunPool core into the repository | Plausible but unproven | It could remove duplicate lifecycle code, but the current pool is substantially coupled to lanes, provider control, paths, and artifacts. A premature split would turn ordinary Metaproc work into coordinated cross-repository releases. |
| A durable run-pool daemon or job service | Poor fit | Persistence, retry, workflow state, and remote job management would duplicate Metaproc and enlarge the trusted failure boundary. |

The recommendation is to proceed with the standalone safety core, `ProcessMonitor`,
`MonitoredProcess`, `SafeProcess`, guard, and broker because those form a useful product
and a coherent failure boundary on their own.
The first package stops there.
Full `SafeRunPool` extraction is a later, optional decision after Metaproc has operated
against the owned-process boundary; it may legitimately be no-go.

#### One Repository, Layered Surfaces

One standalone repository should contain concentric layers with one-way dependencies:

| Layer | Responsibility | May Depend On |
| --- | --- | --- |
| Safety core | Host samples, pure pressure policy, process identity, tree accounting, signalling, journal records, and replay | Python standard library and platform primitives |
| Process-monitor API | Existing-target identity, tree observation, cadence loop, and explicitly authorized guard actions | Safety core only |
| Broker/sentinel | Cross-client claims, launch pacing, embargoes, incidents, and independent containment | Safety core |
| Owned-process API | Admission handshake, process-group creation, identity registration, wait, cancellation, and cleanup | Safety core and broker client |
| CLI adapters | `watch`, `run`, `status`, and `replay` argument parsing and rendering | Corresponding library services |
| Metaproc adapter | Profile translation, lifecycle hooks, and event projection | Owned-process API |
| Later optional pool | Bounded submission, ordering, concurrency, drain, and result collection | Owned-process API, only after the later extraction gate passes |

Dependency direction is an enforceable contract.
The safety core and process-monitor API must not import the broker, owned-process API,
CLI adapters, or Metaproc.
`watch` must start immediately and remain useful when no broker socket exists.
The base guard path should retain the current standard-library-only hot path; optional
packaging or integration dependencies must not be imported by that path.

This produces one implementation with several deliberately small interfaces:

```text
watch CLI ── ProcessMonitor ─────────┐
replay and status CLI ───────────────┤
broker/sentinel ─────────────────────┼── safety core ── macOS/Linux backends
run CLI ── SafeProcess ──────────────┤
Metaproc adapter ────────────────────┘
```

The diagram shows code reuse, not runtime requirements.
`watch` and offline `replay` do not start the broker.
Owned `run` and authoritative Metaproc launches use the broker for host-wide
coordination unless the operator selects an explicit unsafe mode.
There is no install-time daemon; the first authoritative client elects the per-user
broker, and it exits after an idle period.

#### Owned Launch and Existing-Process Monitoring

Owned launch and existing-process monitoring are peer product modes over one policy
core, not a primary implementation plus a compatibility utility:

| Contract | Owned `run` | Monitored `watch` |
| --- | --- | --- |
| Target lifecycle | The runtime prepares and launches the target | The target already exists |
| Host admission | Claim granted before target execution | Cannot be applied retroactively |
| Identity | PID, create time, session, and isolated process group registered before `exec` | PID and create time fenced after discovery; descendants enumerated as the tree changes |
| Containment | Process-group signalling and bounded reap are authoritative | Inherited process groups are untrusted; any authorized action walks the fenced tree |
| Default action authority | Enforce the configured owned-process policy | Observe and journal only; intervention is an explicit operator choice |
| Runtime dependency | Broker required for an authoritative host claim | Safety core only; broker publication is optional |

Both modes use the same normalized host samples, pressure state machine, metric-scope
labels, event vocabulary, journal schema, and replay engine.
They use distinct request and handle types so a caller cannot accidentally acquire
owned-only operations by toggling a flag on a monitored target.

Owned mode provides the authoritative safety guarantee because it controls the launch
before the memory-heavy command executes.
Its standalone interface is conceptually:

```text
safeproc run --profile <resource-profile> -- <command> [args...]
```

A short-lived launch wrapper may inherit the caller’s environment, working directory,
stdio file descriptors, and other launch state.
It obtains a host claim, creates a new session and process group, records its PID,
create time, and process-group identity with the broker, and only then replaces itself
with the target command.
Starting a side monitor immediately after an ordinary subprocess launch does not satisfy
this contract: the target may allocate or fork before registration, and the monitor
cannot prove that the inherited process group contains only the target tree.
The supervisor should create that wrapper and its process group atomically with a
`posix_spawn`-class primitive rather than forking the memory-heavy parent or using a
Python `preexec_fn`. When a platform requires per-child setup before the target starts,
the minimal spawned wrapper performs the setup and calls `exec`; enabling a safety
option must not silently select a higher-risk parent launch path.
The long-lived broker does not need to receive or persist credential-bearing environment
values. If the wrapper or client dies during the handshake, identity-based stale
reclamation keeps the reservation conservative until it can prove that no target
survived.

Metaproc builds on this owned-process boundary beneath `LocalBackend`; a later pool
decision does not change that seam.
Its log redirection and credential scrubbing remain Metaproc responsibilities; the
safety runtime receives only the neutral resource request, redacted labels, client
hooks, and process identity required for admission and containment.

Existing-process monitoring remains an independent, first-class use case for a program
that did not launch through the runtime:

```text
safeproc watch --pid <pid>
```

By default, this command observes and journals without sending signals.
An explicit guard policy may authorize producer pauses, proportional shedding, or
last-resort tree termination.
It runs its own small monitoring loop and journal with no broker requirement.
It may optionally publish observations to a compatible broker, but broker absence must
not change its local guard behavior.
This preserves the memory guard’s usefulness for builds, test suites, compilers, agent
CLIs, data jobs, and other arbitrary process trees.

Monitoring mode carries weaker guarantees.
It cannot retroactively prevent a startup burst or prove that an inherited process group
contains only the target tree.
Pattern-based discovery is a convenience for observation, not sufficient ownership
evidence for an authoritative kill.
After discovery, `ProcessMonitor` must fence the target by PID and create time; it must
stop if that identity no longer matches rather than following a recycled PID. The CLI
and journal must distinguish `owned` launches from `monitored` trees, and destructive
monitored-tree behavior should require explicit operator authorization.

The word *monitor* is intentional.
The runtime does not use `ptrace`, become the target’s parent, prevent the target from
exiting, or otherwise establish an operating-system attachment.

#### Python Process APIs and Later Pool Evaluation

The Python surface should expose related but non-substitutable process contracts:

| Boundary name | Contract |
| --- | --- |
| `SafeProcess` | Accepts a prepared launch and resource request, establishes admission and containment before target execution, and returns typed lifecycle events and a terminal result |
| `ProcessTarget` | Identifies an existing root by PID and create time without implying ownership or attachment |
| `ProcessMonitor` | Accepts a `ProcessTarget`, runs the observation and journal loop, and returns a `MonitoredProcess` handle |
| `MonitoredProcess` | Represents the fenced existing target and exposes observation plus only those interventions explicitly authorized by the caller |

The services share value types and policy services rather than a broad base class whose
methods imply equal authority.
Requests should be keyword-only and make destructive policy, deadline clock, and metric
scope explicit; the request type and persisted target record declare the supervision
mode. Results and events should be additive and nonexhaustive so new terminal reasons do
not break older clients.
The API must support many simultaneous owned and monitored processes without installing
per-run global signal handlers; signal forwarding belongs to an explicit caller policy
or one shared application-level router.

Metaproc must use `SafeProcess` for launches it controls rather than launch normally and
start a monitor afterward.
`ProcessMonitor` serves standalone integrations, diagnostics, and legacy workloads that
cannot yet adopt owned launch.
A `MonitoredProcess` cannot be promoted to owned after the target exists.

The first package does not define `SafeRunPool`, submission, queue, or pool-result
types, and it does not ship a `pool` CLI. Metaproc retains its RunPool and calls
`SafeProcess` at the launch boundary.

After the package and the Metaproc integration have stabilized, a separate vertical
slice may evaluate whether Metaproc should delegate its queue and adaptive controller.
Before any public pool API is designed, that slice must prove all of these conditions:

1. The standalone pool imports no Metaproc model, path, event, status, adapter,
   credential, or artifact module.
2. Metaproc’s adapter contains translation and hooks, not another queue, admission
   algorithm, process-tree monitor, or cleanup implementation.
3. Representative Metaproc-only changes to execution lanes, provider backoff, retry
   classification, and artifacts require no standalone release.
4. Representative platform changes to macOS telemetry, Linux PSI, process identity, and
   catastrophic containment require no Metaproc implementation change beyond a version
   update and projection tests.
5. Metaproc unit tests can use an in-process fake broker and deterministic platform
   provider; ordinary development does not require a live daemon or destructive host
   probes.
6. Cancellation, shutdown, prepared-launch hooks, external capacity inputs, and event
   ordering can be expressed without callbacks that reproduce Metaproc internals.
7. Installed-package and cross-version tests demonstrate a tolerable release workflow,
   including one older compatible broker and an explicit incompatible-version failure.

If the gate passes, a later plan may introduce `SafeRunPool`, an optional finite-batch
`pool` CLI, and a migration in which `metaproc.runpool` becomes a compatibility facade
over the standalone pool.
If it fails, Metaproc retains one scheduling queue and builds directly on the standalone
owned-process API. The rejected outcome is a permanent Metaproc queue feeding a second
full-featured queue.

This gate makes the abstraction falsifiable.
The standalone project is justified by process stability and the daemonless monitor;
pool extraction is not part of its initial success criteria.

Development velocity is part of the contract, not an informal preference.
Metaproc pins a compatible released runtime and talks through versioned neutral models;
its default unit tests use deterministic in-process fakes.
Only changes to process ownership, host admission, telemetry, or shared lifecycle
semantics should require a standalone-runtime release.
If ordinary work on lanes, adapters, provider policy, retries, or artifacts repeatedly
needs lockstep edits, the pool boundary has failed and stays inside Metaproc.

#### Runtime and Metaproc Ownership

The runtime owns only generic local process-management and safety facts:

- phase-aware resource requests, admission waiters, reservations, and launch spacing
- macOS and Linux telemetry, normalized pressure state, and policy evaluation
- monitored-target identity, changing-tree observation, and explicitly authorized
  intervention
- process identity, session and process-group creation, descendant cleanup, and
  identity-fenced signalling
- generic host claims, incidents, journals, status, and protocol compatibility

Metaproc continues to own:

- DAG readiness, execution lanes, adapter and credential selection, and prompt creation
- task priority and restartability as authored policy; it passes the resolved values to
  the safety request
- provider- and credential-pressure policy
- retries, resume semantics, output validation, failure classification, and run
  artifacts
- Metaproc-specific events and operator projections

The standalone runtime must not read a Metaproc run directory or import Metaproc models.
Metaproc maps its execution profile and `ProcessConfig` into neutral process requests,
then projects generic events into its existing event and status surfaces.
This avoids making either project’s persisted artifacts an accidental public API of the
other.

Submission, ordering, adaptive capacity, cancellation, drain, and pool-result collection
remain Metaproc responsibilities in the first package integration.
They move only if the later extraction gate passes.

#### Distribution and Compatibility

One repository and distribution should ship the shared core and thin CLI and Python
surfaces so monitored `watch`, owned `run`, the broker, and replay cannot drift onto
different safety policies.
This does not make every component mandatory at runtime.
The base `watch` installation and launch path must stay small, have no
background-service requirement, and avoid optional dependency imports.
A native implementation is warranted only if measured scheduling or allocation
starvation shows that the Python sentinel cannot meet its cadence contract.

The broker protocol and persisted journal need their own version tokens, independent of
the Python package version and Metaproc artifact versions.
Client and broker version skew must negotiate a compatible protocol or fail closed.
Only one compatible broker may own a per-user namespace; two installed package versions
must not run competing pressure policies on the same processes.

Publication should begin as a `0.x` standalone CLI and library without a pool API or
`pool` command, with both macOS and Linux CI, replay fixtures, packaged-artifact smoke
tests, and destructive actions disabled by default outside owned-launch mode.
The project may incubate as a neutral package beside Metaproc while the dependency and
packaging gates are tested, but should move to an independent repository only after the
core contracts are stable enough to avoid lockstep edits.
Metaproc then consumes a versioned release under its supply-chain policy, not a vendored
copy, submodule, or permanent relative-path dependency.
Metaproc should consume it in shadow mode before making it an authoritative dependency.
When it becomes required for supported local launches, missing or incompatible runtime
versions cause an explicit launch refusal unless the operator supplies the documented
unsafe override.

### Platform Backends

The policy and artifact schemas are cross-platform.
Platform providers supply measurements and optional containment.

**macOS provider:**

- budget from free, inactive, and purgeable pages, preserving the current reclaimable
  definition
- consume the kernel VM-pressure state exposed to userspace rather than treating
  `kern.memorystatus_level` as a budget
- read host statistics, swap, and per-process physical footprint without forking in the
  critical sampling path
- use a sleep-aware continuous clock for operator wall deadlines and an active monotonic
  clock for startup work that cannot progress while the host sleeps; persist the clock
  domain with each deadline
- capture terminal CPU time and peak RSS from the child wait result for calibration,
  normalizing platform units before writing portable records
- track compressor growth and the distance to swap-volume exhaustion separately from
  ordinary disk pressure
- use process-group termination because macOS provides no cgroup-equivalent containment
  boundary for this workload

Apple describes memory pressure as a composite of free memory, swap rate, wired memory,
and file cache. The XNU
[memorystatus notification documentation](https://github.com/apple-oss-distributions/xnu/blob/main/doc/vm/memorystatus_notify.md)
documents the kernel pressure states and userspace notification surface.

**Linux provider:**

- retain `MemAvailable` as required headroom
- consume both `some` and `full` Pressure Stall Information, preferably through pollable
  threshold triggers rather than only ten-second averages
- use cgroup-level `memory.current`, `memory.events`, and `memory.pressure` when a
  delegated cgroup v2 hierarchy is available
- optionally place a local run in a cgroup with `memory.high` for throttled reclaim and
  a deliberately looser `memory.max` as hard containment
- fall back to identity-fenced process-group action when cgroup delegation is
  unavailable

The Linux kernel explicitly describes PSI as a signal for dynamic load shedding and
distinguishes partial from full workload stalls.
It describes `memory.high` as a throttle boundary intended for an external manager and
`memory.max` as the hard cgroup limit.
These are stronger containment tools than macOS offers, but they remain capability-gated
and must not make systemd or privileged cgroup delegation a baseline dependency.

### Visibility and Operator Control

The standalone runtime exposes neutral `status`, `events`, and `replay` views.
Metaproc extends its supported views instead of requiring raw claim-directory
inspection:

- `metaproc pool host-admission` shows host state, current headroom and reserve, active
  claims by phase, outstanding startup bytes, waiting requests, next eligible launch,
  sentinel health, and any incident responder.
- `metaproc pool events` includes claim requested, waiting, admitted, launched, settled,
  released, embargoed, preempted, and recovered events.
- `metaproc pool health` records sample cadence, platform pressure evidence, reclaimable
  slope, compressor or PSI signals, and attribution confidence.
- `metaproc pulse` reports an unhealthy or missing sentinel, a stale host sample, and
  admission that is blocked for safety.
- `metaproc kill` and drain operations update claims before and after signalling so the
  host view cannot mistake termination for stale capacity.

Metaproc views must name the runtime protocol and broker version and link each projected
event to its neutral incident or claim ID. The standalone view must remain sufficient to
diagnose non-Metaproc clients without understanding a run directory.

Keep `METAPROC_HOST_MAX_LOCAL_AGENTS` as a simple emergency override.
Add an explicit, noisy unsafe escape hatch for development environments that cannot
create the host claim namespace; ordinary CLI operation must not silently bypass
admission.

### Why the Other Approaches Are Insufficient

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
platform-specific evidence, cross-client ownership, and failure isolation required here.
The proposed layers reuse the working parts of these approaches at their correct scopes.

## API and Artifact Changes

The process types below are design decisions.
`safeproc` is the working package and executable name; registry availability and final
external naming remain extraction gates.

- Define a versioned, project-neutral broker protocol for resource requests, launch
  identity registration, heartbeats, release, host samples, embargoes, and incidents.
  Protocol compatibility is independent of Metaproc and package versions.
- Define separate narrow owned-process and process-monitor interfaces in the standalone
  package. `SafeProcess` owns launch.
  `ProcessMonitor` accepts a `ProcessTarget` and produces a `MonitoredProcess` handle.
  They share process identity, host sample, pressure decision, lifecycle event, and
  journal models, but use distinct requests, results, and available operations.
  The owned request declares launch, clock, resource scopes, and cleanup policy; its
  result distinguishes workload exit, timeout, resource pressure, external signal,
  cancellation, and supervisor failure.
  The monitor request declares identity and observation or intervention authority; it
  never implies admission, operating-system attachment, or process-group ownership.
- Do not define `SafeRunPool`, `PoolSubmission`, or a `pool` CLI in the first package.
  Any later pool surface requires the deferred extraction gate and a separate API
  review.
- Add a Metaproc integration adapter at the owned-process boundary.
  Preserve its scheduler while removing duplicated launch, admission, telemetry, and
  process-cleanup code.
- Give the standalone runtime its own generic JSONL journal and status schema.
  Metaproc consumes and projects those records; the dependency never writes
  `runpool-status.yaml`, Metaproc trace events, or other run artifacts directly.
  Records are additive, versioned, and explicit about clock domain, metric scope,
  `owned` or `monitored` mode, identity confidence, and action authority.
- Add an optional typed `resources.memory` shape and platform overrides to execution
  profiles, with a schema migration that accepts released `ExecutionProfile/0.1` files.
- Version the host admission lease from count slot v1 to resource claim v2. Do not
  rewrite live v1 leases in place; readers support both during rollout, and new writers
  use a separate namespace.
- Add typed host-state, waiter, incident, and sentinel records.
  Every persisted record receives a schema token and an artifact-catalog entry.
- Extend `ConcurrencyPlan` or add a sibling `HostAdmissionPlan` that records authored
  memory shape, selected platform override, reserve policy, cold-start behavior, and
  launch-spacing provenance.
- Add `host_pressure_preempted` to process and failure projections without folding it
  into adapter failure classes.
- Preserve complete environment and file-descriptor semantics through the safe-launch
  wrapper without placing unredacted commands, credentials, prompts, or environment
  values in broker state or journals.
- Preserve existing CLI flags.
  New unsafe bypass or sentinel controls must be explicit and discoverable through
  command help. Monitored `watch` sends no signals unless the operator selects an
  intervention policy explicitly.

## Implementation Plan

### Phase 0: Prove the Reusable Boundary

Implement the repository and package mechanics through the
[Safeproc Local Incubation](plan-2026-09-01-safeproc-local-incubation.md) plan.
The checklist below defines the architectural outcomes that package work must satisfy.

- [ ] Specify neutral process request, result, lifecycle event, resource,
  process-identity, host-sample, journal, and client-broker protocol models without
  importing Metaproc types.
- [ ] Define a platform capability matrix for launch, root, tree, cgroup, and host
  measurements; clock domains; event-driven exit observation; and containment.
  Unsupported scope must be reported rather than approximated without a label.
- [ ] Split the current guard into pure policy and replay code, process ownership and
  signalling, journaling, and platform providers while preserving its macOS telemetry,
  explicitly selected intervention policy, and failure corpus.
- [ ] Translate the Procguard v1.5.1 immediate-exit, `ESRCH`, process-group,
  zero-timeout, sleep-aware clock, and suspended-process cleanup cases into
  implementation-independent contract tests.
  Record provenance rather than copying source or overstating its formal verification
  coverage.
- [ ] Add a Linux provider using `MemAvailable`, PSI, PSS, and optional cgroup v2
  capabilities; keep one shared policy state machine above both providers.
- [ ] Build `ProcessMonitor`, `MonitoredProcess`, daemonless `watch`, and offline
  `replay` first, with import-boundary tests proving that they do not load the broker,
  owned-process API, optional integrations, or Metaproc.
  Make observation the `watch` default and require an explicit policy for pause,
  shedding, or termination.
- [ ] Build the broker/sentinel and owned-process API, then expose thin `run` and
  `status` commands over those services.
- [ ] Run one policy and journal conformance suite through both process APIs.
  Equivalent normalized evidence must yield the same host classification while the mode
  capability matrix permits different actions.
- [ ] Prove that the owned launch path uses a nonforking parent primitive even when
  resource controls are enabled, supports concurrent instances, and either cleans up or
  transfers ownership on every injected internal error.
- [ ] Choose the standalone project’s license and document provenance for guard-derived
  code, tests, and replay fixtures before publishing them outside their current homes.
- [ ] Implement one vertical Metaproc integration slice at the owned-process boundary
  and run its admission and telemetry decisions in shadow against the current pool.
- [ ] Publish an experimental `0.x` distribution only after macOS and Linux package
  smoke, brokerless `watch`, journal replay, secret-redaction, and client-broker
  version-skew tests pass.
  The published surface must contain no pool API or `pool` CLI.

### Phase 1: Startup-Aware Admission

- [ ] Add the versioned memory-shape model and backward-compatible execution-profile
  parsing.
- [ ] Implement host claim v2 in the standalone broker with serialized decisions,
  inspectable waiters, stale identity reclamation, startup reservations, global launch
  spacing, and coherent count caps.
- [ ] Route scalar agents, fan-out agents, mapped scopes, `run-parallel`, retries, and
  every other local adapter spawn through the owned-process API while retaining
  Metaproc’s existing RunPool as the only queue and adaptive-capacity controller.
- [ ] Remove the superseded in-tree lifecycle, telemetry, and host-admission
  implementations once behavior and artifact projections reach parity.
  Do not remove or wrap the queue and adaptive controller during the initial package
  integration.
- [ ] Change local agent admission to block or fail closed on timeout and state I/O
  failure. Retain only an explicit unsafe development override.
- [ ] Define a redacted, bounded adapter-state fingerprint for calibration without
  persisting conversation content, credentials, or arbitrary absolute paths.
- [ ] In Metaproc’s adapters for supported Gemini versions, disable automatic startup
  session cleanup or use isolated, bounded project state when compatible with the
  adapter contract. Test the mitigation against accumulated history, retain the
  conservative high-spike profile whenever the state regime cannot be proved, and never
  treat a V8 heap cap as admission control.
- [ ] Seed and revalidate the built-in Darwin Gemini profiles; record the CLI version,
  effective state and configuration regime, and diagnostic working-directory context
  with every measurement.
- [ ] Add the host-admission plan, status view, event types, migrations, and operator
  documentation.
- [ ] Prove with separate OS processes that standalone clients and many concurrent
  `run-process` parents share one namespace and cannot overlap more startup reservations
  or launches than the host authority allows.

### Phase 2: Responsive Pressure Control

- [ ] Promote the standalone runtime’s low-overhead macOS and Linux providers to the
  authoritative telemetry source, with adaptive sampling cadence, sample-lag recording,
  and clear capability failures.
- [ ] Add kernel pressure, compressor or reclaimable slope, PSI, swap growth, and
  swap-volume safety signals without conflating them with ordinary low disk.
- [ ] Implement the host pressure state machine and shared launch embargo.
- [ ] Add macOS physical-footprint and Linux PSS or cgroup attribution where available.
- [ ] Implement one-responder pressure shedding with fault attribution, proportional
  rounds, settle windows, identity fencing, and durable preemption semantics.
- [ ] Update `pool`, `pulse`, `stats`, trace, and Metabrowser projections for claims,
  embargoes, preemptions, and recovery.

### Phase 3: Independent Containment and Rollout

- [ ] Complete the automatically elected standalone broker/sentinel with nonforking
  hot-path telemetry, early registration, cadence health, claim-registry ownership, and
  guaranteed producer resume cleanup.
- [ ] Run the sentinel in shadow mode and replay both healthy and failed pressure
  journals before enabling cooperative embargo actions.
- [ ] Add catastrophic identity-fenced termination only after shadow decisions have zero
  destructive false positives across the acceptance corpus.
- [ ] Add capability-gated Linux cgroup v2 placement with `memory.high`, `memory.max`,
  cgroup PSI, and `memory.events`, retaining the non-cgroup fallback.
- [ ] Make startup-aware admission and sentinel health monitoring default for supported
  local CLI runs. Keep destructive shedding staged behind an explicit rollout gate until
  the live soak criteria pass.
- [ ] Remove downstream launch-stagger workarounds only after their workloads prove that
  the host-scoped controller produces equivalent or better pacing and throughput.

### Phase 4: Evaluate Pool Extraction After Stabilization

- [ ] Confirm that Metaproc has consumed a versioned process-safety package through its
  retained RunPool, representative scheduler-only changes required no package release,
  and representative platform-safety changes required no scheduler redesign before
  beginning a pool spike.
- [ ] Prototype neutral extraction of submission, cancellation, adaptive capacity,
  drain, and terminal cleanup without publishing those types.
- [ ] Exercise the seven pool-extraction gates with representative Metaproc-only and
  platform-only changes.
- [ ] Record a pool go/no-go decision.
  On go, write a separate API and migration plan that preserves direct
  `metaproc.runpool` consumers through compatibility re-exports.
  On no-go, keep one Metaproc scheduler over `SafeProcess` permanently.
- [ ] If a public finite-batch pool is still useful independently of Metaproc, specify
  and review it as a separate feature rather than treating it as part of process safety.

Phases 0 through 3 deliberately ship no standalone pool.
Phase 4 is optional follow-up work and does not block the process-safety package.
A durable job server, retry database, or workflow queue is outside this plan.

## Testing Strategy

### Deterministic Tests

- State-machine tests cover every pressure transition, hysteresis, stale-sample rule,
  predictive-versus-measured intervention boundary, and recovery path.
- A shared platform-provider conformance suite feeds synthetic and recorded macOS and
  Linux measurements through the same normalized host-sample contract, including missing
  capabilities and malformed or stale inputs.
- Claim tests cover atomic races, partial writes, mixed profile limits, timeout
  behavior, owner death before child registration, surviving children after owner death,
  PID reuse, clock changes, sleep and wake, and corrupt state.
  Startup reservations use an active clock and cannot expire solely during system sleep.
- Protocol tests cover compatible and incompatible client-broker versions, broker
  replacement, two installed package versions racing for one namespace, and fail-closed
  behavior during upgrades.
- Import-boundary tests prove that core, `watch`, and replay do not import Metaproc, the
  owned-process API, the broker client, or optional integration dependencies.
- Brokerless guard tests start `watch --pid` with no broker namespace and prove that it
  observes and journals without signalling by default, intervenes only under an explicit
  policy, resumes every process it paused, and exits cleanly.
- Process-monitor tests cover PID reuse, target exit during monitoring, ambiguous
  pattern discovery, descendants born during enumeration, reparenting, permission
  failure, and explicit deepest-first subtree termination without signalling an
  inherited process group.
- Owned-launch tests prove that the wrapper obtains admission before target execution,
  preserves working directory and file descriptors, creates an isolated process group,
  registers exact identity, does not fork the supervising parent, and leaves no
  surviving descendants after success, cancellation, timeout, or injected monitor
  failure.
- Monitor-composition tests disable or zero each deadline and governor in turn while
  proving that every other configured monitor, heartbeat, claim, and cleanup path
  remains active.
- Concurrency tests run many `SafeProcess` instances in one application process while
  delivering termination signals; no instance may consume another instance’s event or
  replace the caller’s signal handlers.
- Exit-observation tests cover a target that exits before watcher registration, `ESRCH`
  and `ECHILD` races, process-group signalling fallback, and immediate exit codes
  without misclassifying completion as timeout.
- Accounting tests distinguish root physical footprint, tree totals, cgroup usage, host
  headroom, and terminal peak RSS. Portable records preserve scope and normalize the
  macOS and Linux `ru_maxrss` unit difference.
- Redaction tests prove that environment credentials, prompt text, and unredacted
  command arguments never enter broker state or the portable journal.
- CLI and Python contract tests drive the same owned-process, process-monitor, and
  policy services and produce equivalent lifecycle and journal records for equivalent
  requests.
- Passive-profiling tests prove that observation never pauses or signals a producer and
  remains distinct from a dry-run intervention mode that may simulate producer control.
- Cross-mode policy tests feed the same host evidence through owned launch and
  existing-process monitoring.
  They require identical pressure classification and journal vocabulary, then verify
  that only owned mode reports pre-execution admission and authoritative process-group
  containment.
- A scaled spike worker allocates quickly, holds, settles, and exits so startup
  reservations and global spacing can be tested without multi-gigabyte CI allocations.
- Adapter-profile fixtures distinguish clean state, accumulated project history, and
  retention-disabled state.
  Gated Gemini tests verify that the supported version’s mitigation prevents the known
  startup scan before selecting the low profile.
- Multi-process tests start independent Metaproc parents, not merely two RunPool objects
  in one event loop.
- Failure injection blocks a RunPool monitor, backend launch, backend kill, and claim
  heartbeat while proving that admission and the sentinel retain ownership.
- Preemption tests prove one action per incident, correct process-group targeting,
  durable `host_pressure_preempted` state, bounded retry, and no adapter-failure
  misclassification.
- Attribution tests prove that outside pressure closes admission but does not authorize
  a destructive Metaproc action that cannot recover enough memory.
- Property tests and fuzz targets cover parsers, protocol records, time arithmetic, and
  pure policy transitions.
  Model checking is optional and reports only the production functions and bounded state
  it actually exercises; it is not evidence that the FFI, signal, or process-monitoring
  system is formally verified.
- Phase 4 adds pool tests for fair ordering, priority aging, external capacity changes,
  cancellation while queued and launching, bounded shutdown, and the rule that no
  submission can launch after close.
  These tests are not part of the initial package contract.

### Replay and Live Tests

- Build an anonymized pressure-journal corpus containing healthy startup spikes, false
  predictive alarms, sustained critical episodes, outside-load incidents, sleep/wake,
  and sampling starvation.
- Replay every policy revision over the complete corpus.
  Predictive signals may create false embargoes; measured destructive actions must have
  no healthy-run false positives.
- On macOS, run gated live tests for concurrent spike-shaped process groups, physical
  footprint, kernel pressure transitions, producer freeze/resume, and sentinel survival
  when the parent event loop is blocked.
- On Linux, test PSI triggers and process-group fallback everywhere; test cgroup
  throttling and OOM containment only where delegation is available.
- Run the standalone packaged artifact on macOS and Linux, including one standalone
  client and one Metaproc client sharing the same broker and launch timeline.
- Publish a compile-and-execute matrix that distinguishes native tests from
  cross-builds; both macOS and Linux backends must run their process-lifecycle suites on
  the supported architectures before a stable release.
- Run packaged `watch --pid` on macOS and Linux with no broker state and no Metaproc
  installation, and measure startup time, resident memory, and sampling cadence.
- Run representative adapter soaks across clean, accumulated, and mitigated client-state
  regimes. Hold client state constant across small and large repositories to detect false
  working-directory explanations, and include fresh launches and bursty resumes.

### Acceptance Criteria

- No supported local agent launch can occur without a recorded claim unless the operator
  supplied the explicit unsafe override.
- Concurrent standalone commands and independent Metaproc runs obey one host-wide
  launch-spacing timeline and one startup reservation budget.
- A blocked or crashed RunPool does not make host admission fail open.
- The broker and sentinel continue sampling and enforcing an embargo while a Metaproc
  event loop is blocked, and incompatible or missing clients fail closed.
- `watch --pid` remains independently usable with no broker, daemon, or Metaproc import.
  It observes without signalling by default, exposes explicit monitored-process guard
  policies when requested, and the CLI and Python surfaces use the same policy and
  journal implementation.
- The first published package contains no pool API, submission queue, pool-result type,
  or `pool` CLI. Metaproc retains one local subprocess queue and adaptive controller and
  launches through `SafeProcess` without feeding another queue.
- Phase 4 cannot alter the first-package acceptance decision.
  If a later pool extraction proceeds, its vertical slice must record a go/no-go result
  before any public pool type or compatibility facade is published.
- Metaproc’s ordinary unit-test path uses deterministic fakes and never requires a live
  broker, destructive pressure action, or separate repository checkout.
- A change confined to Metaproc lanes, provider backoff, retries, or artifacts can ship
  without a standalone-runtime release; a platform safety change can ship without
  modifying Metaproc scheduling code.
- Owned launch establishes admission, isolated process-group identity, and cleanup
  authority before the target command executes; starting a monitor after an ordinary
  spawn does not satisfy this criterion.
- Owned mode never signals an unregistered process identity.
  Monitoring mode is visibly weaker, revalidates PID plus create time before destructive
  actions, never signals an inherited process group, and cannot perform an
  argv-pattern-authorized kill.
- The same normalized host evidence produces the same pressure classification and
  journal vocabulary in owned and monitored-process modes; their differences are
  confined to lifecycle authority, safe actions, and stated guarantees.
- The supervising parent does not call `fork` on the authoritative launch or sampling
  path, including when per-process limits are enabled.
- Many concurrent owned processes can share one Python process without global
  signal-handler interference, and every injected post-spawn supervisor failure leaves
  the process group reaped or visibly owned by the broker or sentinel.
- Every journaled deadline names its clock, every memory value names its scope, and a
  system sleep cannot release a startup claim for a still-live process.
- Healthy workload replays produce no preemptions or sentinel terminations.
- Critical replays take at most one proportional shedding action per settle interval and
  recover without unrelated process signals.
- Non-spiky adapters retain near-current steady-state throughput after ramp-up; a safety
  change that merely serializes every adapter does not pass.
- A Gemini-class spike workload can sustain its intended steady fan-out on the
  calibration host without entering catastrophic pressure.
- A supported Gemini profile may use the low startup regime only after the configured
  state mitigation is verified.
  Unknown or unverified state uses the conservative high-spike profile and host pacing;
  a heap cap does not satisfy this criterion.
- `make verify` and installed-wheel macOS and Linux smoke tests pass at the exact
  landing commit.

## Rollout Plan

1. Define the neutral records, extract the guard policy and Darwin provider, add the
   Linux provider, and package `ProcessMonitor`, brokerless `watch`, and offline
   `replay`. This first vertical slice must be useful without Metaproc; observation is
   the default and destructive monitored-process behavior remains explicitly gated.
2. Add the broker/sentinel, `SafeProcess`, `run`, and `status`, then publish the
   standalone runtime as an experimental `0.x` package with no pool surface after the
   package and version-skew gates pass.
3. Add the Metaproc client at the owned-process boundary in shadow mode while retaining
   Metaproc’s existing RunPool.
   Existing count admission remains enforceable while the external runtime records host
   claim v2 decisions and Metaproc compares both event streams.
4. Enable startup-aware admission for built-in Gemini profiles, then for all tested
   local profiles. Unknown profiles use cold-start serialization and a prominent status
   reason.
5. Make the standalone broker’s claim v2 authority the default, remove scalar fail-open
   behavior, and adopt the package as a required local-runtime dependency only after
   protocol, release, and supply-chain gates pass.
   Retain the count-only namespace only for released-run compatibility and inspection.
6. Enable the host pressure embargo after replay validation.
   Enable preemption separately after live macOS and Linux smoke.
7. Start the sentinel in shadow mode by default, promote cooperative actions after soak,
   and promote catastrophic containment only with the zero-destructive-false-positive
   gate satisfied.
8. Update the shipped RunPool architecture, operator reference, process-framework
   theory, artifact catalog, execution-profile docs, and downstream migration guidance
   in the same release as each behavior change.
9. Gather maintenance and operating evidence from the standalone package and the
   retained-RunPool integration before beginning the optional Phase 4 pool extraction
   spike.
10. If Phase 4 passes, plan the pool API and migration separately.
    Consider a durable job-service facade only after an independent consumer needs
    persistence, retry, or workflow semantics beyond a safe subprocess pool.

## Open Questions

- What reserve fraction and absolute reserve should ship on different host sizes?
  Choose them through trace replay and live calibration, not from one workstation.
- Should a reliable attributable footprint release part of a startup reservation early,
  or is the declared startup window cheap enough to keep the first version simpler?
- What redacted adapter-version, state, and configuration fingerprint is sufficient for
  session calibration without creating a high-cardinality persistent cache or treating
  an absolute working-directory path as the cause?
- At what validated boundary should catastrophic sentinel termination become default
  rather than opt-in?
- Should the authoritative launch handshake remain a short-lived wrapper that inherits
  secrets and file descriptors, or can a broker-spawn API meet Metaproc’s logging and
  credential-isolation contracts without widening the trusted state surface?
- Which project owns releases and protocol support for the standalone runtime, and how
  long must Metaproc support an older broker during rolling upgrades?
- Should the base distribution have no third-party runtime dependencies, or is it enough
  to guarantee that `watch` and the sentinel hot path import only the standard library?
- If the later extraction gate passes, should `SafeRunPool` be public or remain an
  internal compatibility layer for Metaproc?
- Does the working `safeproc` project and executable name remain available and clear at
  extraction time? Recheck both package and repository registries before creation.
- Which current RunPool configuration and event fields are generic enough to move, and
  which remain Metaproc projections or compatibility shims?
- Where cgroup delegation exists, should one cgroup contain each run, each execution
  profile, or the whole local Metaproc host namespace?
  The answer affects both fairness and what one OOM event is allowed to kill.

## References

- [RunPool architecture](../../../../src/metaproc/docs/arch-runpool.md)
- [Process framework theory: readiness versus admission](../../../../src/metaproc/docs/process-framework-theory.md#resources-readiness-versus-admission)
- [RunPool design backlog](../../design/backlog/arch-runpool-backlog.md)
- [Agent CLI startup-memory research](../../research/research-2026-09-01-agent-cli-memory-usage.md)
- [Host memory-accounting research](../../research/research-2026-09-01-host-memory-accounting-and-control.md)
- [Safeproc Local Incubation](plan-2026-09-01-safeproc-local-incubation.md)
- [Standalone macOS memory guard](https://gist.github.com/jlevy/5b43e0d44166b9c7fe8157ee938cb0d5)
- [Procguard v1.5.1 source](https://github.com/denispol/procguard/tree/v1.5.1)
- [GNU Parallel memory and launch controls](https://www.gnu.org/software/parallel/parallel.html)
- [Apple Activity Monitor memory accounting](https://support.apple.com/guide/activity-monitor/view-memory-usage-actmntr1004/mac)
- [XNU memorystatus notifications](https://github.com/apple-oss-distributions/xnu/blob/main/doc/vm/memorystatus_notify.md)
- [Linux Pressure Stall Information](https://docs.kernel.org/accounting/psi.html)
- [Linux cgroup v2 memory controller](https://docs.kernel.org/admin-guide/cgroup-v2.html#memory)

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
