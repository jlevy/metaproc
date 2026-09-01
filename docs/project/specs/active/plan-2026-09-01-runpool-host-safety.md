---
title: RunPool Host Safety Envelope
description: Design a standalone safety-focused subprocess pool with startup-aware host protection for Metaproc and other macOS and Linux clients.
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
The recommended unit is a **safe subprocess-pool runtime**: a small pool and policy
library, one per-user broker/sentinel process, and a standalone command-line interface.
It is more capable than a tree-attached memory guard and narrower than Metaproc itself.
It owns an in-memory command queue, adaptive local concurrency, resource-aware
admission, safe subprocess ownership, host telemetry, and last-resort containment; it
does not own DAGs, adapters, durable retries, output validation, or run artifacts.

The public
[memory guard](https://gist.github.com/jlevy/5b43e0d44166b9c7fe8157ee938cb0d5) supplies
the first macOS backend, decision policy, journal, and failure corpus.
Its attached-tree interface should remain a useful compatibility mode, but it is not the
authoritative launch path.
Metaproc should replace the generic core of its current RunPool with this package, not
stack two local process pools.
Metaproc-specific status, lane projections, provider-pressure policy, and run artifacts
remain in a thin integration layer.

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
- Replace the generic parts of Metaproc’s internal RunPool rather than adding a second
  subprocess scheduler beneath it.
- Keep every admission, wait, pressure transition, preemption, and sentinel action
  attributable through supported status and event surfaces.
- Preserve process-group ownership, retry semantics, resume safety, and compatibility
  with released execution profiles and runtime artifacts.

## Non-Goals

- Protect arbitrary processes that Metaproc did not launch or register.
  The host controller may identify outside pressure, but it must not kill unrelated
  applications.
- Turn a general disk-space warning into a memory kill trigger.
  Swap headroom is relevant on macOS; ordinary low disk remains a separate operational
  problem.
- Make a fixed low `--max-concurrency` the default safety mechanism.
- Promise that an adapter has one universal memory cost.
  CLI version, model, platform, prompt, tools, and effective working directory can all
  change its shape.
- Add Windows support without a reliable telemetry and process-containment design.
- Publish Metaproc’s task scheduler, retry engine, execution lanes, adapters, or run
  artifacts as part of the host-safety library.
- Build a durable job server, workflow scheduler, retry database, or distributed queue
  as part of the standalone pool.
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

### Safety Principles

The design follows five rules.

1. **Admission happens at the scope of the resource.** Memory is a host fact, so no
   per-run controller can be its sole authority.
2. **A process has a memory shape, not one cost.** Admission must distinguish an
   outstanding startup allocation from an already materialized steady working set.
3. **Projection and intervention require different evidence.** A slope or predicted
   time-to-floor may stop new work.
   Only a measured critical state may take work away.
4. **A safety failure fails closed.** A corrupt lease namespace, timeout, or unavailable
   required gauge cannot authorize an otherwise prohibited launch.
5. **The final guard is independent.** An in-process assertion cannot protect against
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
8. A pressure kill targets only a recorded process identity and its owned process group.
9. A host-pressure preemption is durable, distinguishable from an adapter failure, and
   resumable without being misclassified as a deterministic workload error.
10. The independent sentinel can act when all RunPool event loops are unresponsive, and
    it cannot act on unrelated process trees.
11. Broker state and journals contain no credential-bearing environment values, prompts,
    or unredacted command arguments.
12. An absent or protocol-incompatible standalone runtime cannot silently downgrade an
    authoritative launch to unsupervised execution.

### One Host Resource Authority

Replace the count-only slot namespace with a versioned claim namespace owned by one
elected per-user broker.
Its state root must be host-local, private to the effective user, and neutral rather
than living permanently under `.metaproc`:

```text
<host-safety-state>/<host-instance>/local-agents/
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
The first Gemini correction should seed a conservative Darwin startup profile from the
published 4.6 GB and 71-second observations, rounded upward, then remeasure against the
supported Gemini CLI version and the effective configured working directory before
declaring the values stable.
A global 500 MB startup assumption must not remain the tested Gemini default.

An unknown or materially changed profile enters **cold-start calibration**: the host
serializes its first start, observes its peak through a bounded window, and uses the
observed high-water mark with a safety margin for the rest of that host session.
Runtime learning is visible and scoped to the exact adapter version, platform, model,
and working directory context.
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

### Extract a Safe Subprocess Pool, Not Metaproc

Packaging is part of the failure boundary.
Moving the policy into an imported library would improve reuse but would not protect the
host from a blocked or corrupted Metaproc process.

The existing public boundary points toward this extraction.
`runpool/README.md` already supports RunPool as a library for tools that need adaptive
local concurrency without process specs.
`runpool/backend.py`, `runpool/host_admission.py`, `runpool/monitor.py`,
`runpool/semaphore.py`, and `osutils/memory_pressure.py` contain generic lifecycle and
host logic. `runpool/pool.py` mixes more generic submission, cancellation, and
adaptive-capacity logic with Metaproc paths, execution lanes, status, events, and
provider policy. The extraction should separate those responsibilities rather than
publishing the module unchanged.
The generic launch primitive must also shed its current log-filter and
invocation-sidecar coupling; those remain client hooks because they encode Metaproc
artifacts and credential handling.

The publishable distribution should therefore contain four surfaces:

| Surface | Responsibility |
| --- | --- |
| Safe subprocess pool | In-memory submission queue, adaptive concurrency, priorities, cancellation, terminal cleanup, result collection, and generic capacity inputs |
| Policy library | Typed resource claims, pure pressure-state transitions, victim-selection policy, journal replay, and platform interfaces |
| Per-user broker/sentinel process | Host-wide admission, waiter fairness, launch embargo, live claim registry, telemetry cadence, incident pacing, and independent containment |
| Standalone CLI | Safely launch one command or a bounded command set, attach to an existing tree with weaker guarantees, inspect host state, and replay a journal |

The intended composition is:

```text
Metaproc integration ───────┐
standalone CLI ─────────────┼── SafeRunPool / SafeProcess ── broker/sentinel ── OS
other library clients ──────┘
```

The runtime owns only generic local process-management and safety facts:

- phase-aware resource requests, admission waiters, reservations, and launch spacing
- in-memory command submission, fair ordering, local adaptive capacity, cancellation,
  and terminal process cleanup
- macOS and Linux telemetry, normalized pressure state, and policy evaluation
- process identity, session and process-group creation, descendant cleanup, and
  identity-fenced signalling
- generic host claims, incidents, journals, status, and protocol compatibility

Metaproc continues to own:

- DAG readiness, execution lanes, adapter and credential selection, and prompt creation
- task priority and restartability as authored policy; it passes the resolved values to
  the safety request
- provider- and credential-pressure policy; it supplies external pause or capacity
  inputs to the generic pool
- retries, resume semantics, output validation, failure classification, and run
  artifacts
- Metaproc-specific events and operator projections

The standalone runtime must not read a Metaproc run directory or import Metaproc models.
Metaproc maps its execution profile and `ProcessConfig` into neutral pool submissions
and launch requests, then projects generic pool and safety events into its existing
event and status surfaces.
The released `metaproc.runpool` imports remain as compatibility re-exports while callers
migrate; they do not retain a second implementation.
This avoids making either project’s persisted artifacts an accidental public API of the
other.

#### Authoritative and Attached Launch Modes

An authoritative safety guarantee requires control before the memory-heavy process is
created. The strongest standalone interface is therefore conceptually:

```text
host-safety run <resource-profile> -- <command> [args...]
```

The standalone `pool` command is a thin finite-batch facade over the same library API.
It accepts a bounded command stream or manifest, supervises it to terminal results, and
exits; it does not create a durable background job database.

A short-lived launch wrapper may inherit the caller’s environment, working directory,
stdio file descriptors, and other launch state.
It obtains a host claim, creates a new session and process group, records its PID,
create time, and process-group identity with the broker, and only then replaces itself
with the target command.
The long-lived broker does not need to receive or persist credential-bearing environment
values. If the wrapper or client dies during the handshake, identity-based stale
reclamation keeps the reservation conservative until it can prove that no target
survived.

Metaproc can use the same wrapper or an equivalent library handshake beneath
`LocalBackend`. Its log redirection and credential scrubbing remain Metaproc
responsibilities; the safety runtime receives only the neutral resource request,
redacted labels, and process identity required for admission and containment.

An attached mode remains useful for protecting an existing program that did not launch
through the runtime:

```text
host-safety watch --pid <pid>
```

That mode carries weaker guarantees.
It cannot retroactively prevent a startup burst or prove that an inherited process group
contains only the target tree.
Pattern-based discovery is a convenience for observation, not sufficient ownership
evidence for an authoritative kill.
The CLI and journal must distinguish `owned` launches from `attached` trees, and
destructive attached-tree behavior should require explicit operator authorization.

#### Keep the Standalone Pool Narrow

The pool is a first-class package surface, not a durable job server.
It accepts prepared subprocess launches for one client session, orders pending work,
applies local and host admission, reports results, supports cancellation and drain, and
guarantees terminal process-group cleanup.
That is enough for Metaproc to replace its internal RunPool core and for a standalone
CLI to run a bounded set of commands safely.

The pool does not persist a job database, traverse dependencies, retry failed work after
restart, discover adapters, validate outputs, or decide whether a preempted command
should run again. Metaproc remains the durable scheduler above it and supplies provider
or credential pauses through a generic external-capacity input.
Standalone callers that need retry or resume build that policy above the same result
stream.

This boundary avoids the extra abstraction layer in the rejected design:

```text
Metaproc scheduler ──> standalone SafeRunPool ──> host broker/sentinel
```

The old Metaproc pool core is removed after compatibility migration; it does not remain
as another queue in front of the extracted pool.
A future durable job manager may consume `SafeRunPool`, but it is a separate product and
not part of this safety dependency.

#### Distribution and Compatibility

The first implementation can remain Python and preserve the guard’s standard-library hot
path: Darwin `ctypes`, Linux `/proc` and PSI, and capability-gated cgroup support.
A native implementation is warranted only if measured scheduling or allocation
starvation violates the sentinel cadence contract.

The broker protocol and persisted journal need their own version tokens, independent of
the Python package version and Metaproc artifact versions.
Client and broker version skew must negotiate a compatible protocol or fail closed.
Only one compatible broker may own a per-user namespace; two installed package versions
must not run competing pressure policies on the same processes.

Publication should begin as a `0.x` standalone CLI and library with both macOS and Linux
CI, replay fixtures, packaged-artifact smoke tests, and destructive actions disabled by
default outside owned-launch mode.
Once the neutral contracts stabilize, the project should have an independent repository
and release cadence.
Metaproc consumes a versioned release under its supply-chain policy, not a vendored
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
| Publish the current memory guard unchanged | Proven last-resort evidence, policy, and intervention | Its attached-tree contract acts after launch, is macOS-only, and cannot provide authoritative cross-client admission. It should become one mode of the broader runtime. |
| Delegate to GNU Parallel | Mature command queue with launch delay, memory admission, suspension, and retry | Its generic free-memory rules do not provide the required macOS pressure accounting, startup-phase claims, cross-client identity registry, or independent sentinel. |
| Publish Metaproc’s current RunPool unchanged | Reuses a capable scheduler and local process manager | The module mixes a reusable pool core with Metaproc paths, lanes, events, status, provider policy, and artifact compatibility. Extract the generic core and retain a Metaproc adapter instead. |
| Use only OS hard limits | Strong Linux containment | macOS has no equivalent local cgroup boundary, and a hard byte ceiling alone can kill healthy startup transients. |
| Keep the current adaptive semaphore | Good local throughput controller | Every pool sees only its own capacity, reduction is non-preemptive, and the scalar memory estimate misses the burst shape. |

GNU Parallel demonstrates that a standalone command pool can use both
[launch spacing and memory-aware admission](https://www.gnu.org/software/parallel/parallel.html).
The safe subprocess-pool runtime keeps those useful mechanics while adding the
platform-specific evidence, cross-client ownership, and failure isolation required here.
The proposed layers reuse the working parts of these approaches at their correct scopes.

## API and Artifact Changes

The names below describe boundaries; package and public type naming remain a Phase 0
decision.

- Define a versioned, project-neutral broker protocol for resource requests, launch
  identity registration, heartbeats, release, host samples, embargoes, and incidents.
  Protocol compatibility is independent of Metaproc and package versions.
- Define narrow `SafeRunPool`, `SafeProcess`, `PoolSubmission`, `ProcessResult`, and
  broker-client interfaces in the standalone package.
  Pool configuration accepts generic capacity and pause inputs without knowing why a
  client imposed them.
- Replace Metaproc’s generic pool implementation with an integration adapter and
  compatibility re-exports for released `metaproc.runpool` imports.
  The adapter maps execution lanes, provider policy, status, and artifacts without
  maintaining a second scheduler.
- Give the standalone runtime its own generic JSONL journal and status schema.
  Metaproc consumes and projects those records; the dependency never writes
  `runpool-status.yaml`, Metaproc trace events, or other run artifacts directly.
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
  command help.

## Implementation Plan

### Phase 0: Establish the Reusable Boundary

- [ ] Specify neutral pool submission, result, resource, process-identity, host-sample,
  journal, and client-broker protocol models without importing Metaproc types.
- [ ] Split the generic queue, adaptive semaphore, launch backend, cancellation, drain,
  and terminal-cleanup logic from Metaproc paths, lanes, events, status, and provider
  policy.
- [ ] Split the current guard into pure policy and replay code, process ownership and
  signalling, journaling, and platform providers while preserving its macOS behavior and
  failure corpus.
- [ ] Add a Linux provider using `MemAvailable`, PSI, PSS, and optional cgroup v2
  capabilities; keep one shared policy state machine above both providers.
- [ ] Build the standalone `run`, `pool`, `watch`, `status`, and `replay` surfaces.
  Owned `run` mode gets the strong process-group contract; attached `watch` mode remains
  explicitly weaker.
- [ ] Choose the standalone project’s license and document provenance for guard-derived
  code, tests, and replay fixtures before publishing them outside their current homes.
- [ ] Add the Metaproc integration adapter and initially run the extracted pool’s
  admission, capacity, and telemetry decisions in shadow against the current pool.
- [ ] Preserve direct `metaproc.runpool` consumers through tested compatibility
  re-exports and a migration notice; do not keep a forked implementation.
- [ ] Publish an experimental `0.x` distribution only after macOS and Linux package
  smoke, journal replay, secret-redaction, and client-broker version-skew tests pass.

### Phase 1: Startup-Aware Admission

- [ ] Add the versioned memory-shape model and backward-compatible execution-profile
  parsing.
- [ ] Implement host claim v2 in the standalone broker with serialized decisions,
  inspectable waiters, stale identity reclamation, startup reservations, global launch
  spacing, and coherent count caps.
- [ ] Route scalar agents, fan-out agents, mapped scopes, `run-parallel`, retries, and
  every other local adapter spawn through the extracted pool or its one-process
  owned-launch API.
- [ ] Remove the superseded in-tree queue, adaptive controller, local lifecycle, and
  host-admission implementations once behavior and artifact projections reach parity.
- [ ] Change local agent admission to block or fail closed on timeout and state I/O
  failure. Retain only an explicit unsafe development override.
- [ ] Seed and revalidate the built-in Darwin Gemini startup profile; record the
  effective working directory and CLI version with every measurement.
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

The standalone in-memory command pool is part of these phases because it replaces
Metaproc’s current local pool core.
A durable job server, retry database, or workflow queue is not; any such product should
consume the completed safe pool without expanding its subprocess-safety contract.

## Testing Strategy

### Deterministic Tests

- State-machine tests cover every pressure transition, hysteresis, stale-sample rule,
  predictive-versus-measured intervention boundary, and recovery path.
- Claim tests cover atomic races, partial writes, mixed profile limits, timeout
  behavior, owner death before child registration, surviving children after owner death,
  PID reuse, clock changes, and corrupt state.
- Protocol tests cover compatible and incompatible client-broker versions, broker
  replacement, two installed package versions racing for one namespace, and fail-closed
  behavior during upgrades.
- Pool tests cover fair ordering, priority aging, external capacity changes,
  cancellation while queued and launching, bounded shutdown, and the rule that no
  submission can launch after close.
- Owned-launch tests prove that the wrapper obtains admission before target execution,
  preserves working directory and file descriptors, creates an isolated process group,
  registers exact identity, and leaves no surviving descendants after terminal cleanup.
- Redaction tests prove that environment credentials, prompt text, and unredacted
  command arguments never enter broker state or the portable journal.
- A scaled spike worker allocates quickly, holds, settles, and exits so startup
  reservations and global spacing can be tested without multi-gigabyte CI allocations.
- Multi-process tests start independent Metaproc parents, not merely two RunPool objects
  in one event loop.
- Failure injection blocks a RunPool monitor, backend launch, backend kill, and claim
  heartbeat while proving that admission and the sentinel retain ownership.
- Preemption tests prove one action per incident, correct process-group targeting,
  durable `host_pressure_preempted` state, bounded retry, and no adapter-failure
  misclassification.
- Attribution tests prove that outside pressure closes admission but does not authorize
  a destructive Metaproc action that cannot recover enough memory.

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
- Run representative adapter soaks from small working directories and large
  repositories, including fresh launches and bursty resumes.

### Acceptance Criteria

- No supported local agent launch can occur without a recorded claim unless the operator
  supplied the explicit unsafe override.
- Concurrent standalone commands and independent Metaproc runs obey one host-wide
  launch-spacing timeline and one startup reservation budget.
- A blocked or crashed RunPool does not make host admission fail open.
- The broker and sentinel continue sampling and enforcing an embargo while a Metaproc
  event loop is blocked, and incompatible or missing clients fail closed.
- Metaproc has one local subprocess queue and adaptive controller after migration; its
  compatibility module delegates to the standalone pool rather than running parallel
  scheduling logic.
- Owned-launch mode never signals an unregistered process identity; attached mode is
  visibly weaker and cannot perform an argv-pattern-authorized kill by default.
- Healthy workload replays produce no preemptions or sentinel terminations.
- Critical replays take at most one proportional shedding action per settle interval and
  recover without unrelated process signals.
- Non-spiky adapters retain near-current steady-state throughput after ramp-up; a safety
  change that merely serializes every adapter does not pass.
- A Gemini-class spike workload can sustain its intended steady fan-out on the
  calibration host without entering catastrophic pressure.
- `make verify` and installed-wheel macOS and Linux smoke tests pass at the exact
  landing commit.

## Rollout Plan

1. Define the neutral protocols, extract the guard policy and Darwin provider, add the
   Linux provider, and publish the standalone runtime as an experimental `0.x` package.
   Begin with observation, replay, owned single-command launch, and an in-memory bounded
   pool; attached destructive behavior remains explicitly gated.
2. Add the Metaproc client in shadow mode.
   Existing count admission remains enforceable while the external runtime records host
   claim v2 decisions and Metaproc compares both event streams.
3. Enable startup-aware admission for built-in Gemini profiles, then for all tested
   local profiles. Unknown profiles use cold-start serialization and a prominent status
   reason.
4. Make the standalone broker’s claim v2 authority the default, remove scalar fail-open
   behavior, and adopt the package as a required local-runtime dependency only after
   protocol, release, and supply-chain gates pass.
   Retain the count-only namespace only for released-run compatibility and inspection.
5. Enable the host pressure embargo after replay validation.
   Enable preemption separately after live macOS and Linux smoke.
6. Start the sentinel in shadow mode by default, promote cooperative actions after soak,
   and promote catastrophic containment only with the zero-destructive-false-positive
   gate satisfied.
7. Update the shipped RunPool architecture, operator reference, process-framework
   theory, artifact catalog, execution-profile docs, and downstream migration guidance
   in the same release as each behavior change.
8. Consider a separate durable job-service facade only after an independent consumer
   needs persistence, retry, or workflow semantics that the safe subprocess pool does
   not provide.

## Open Questions

- What reserve fraction and absolute reserve should ship on different host sizes?
  Choose them through trace replay and live calibration, not from one workstation.
- Should a reliable attributable footprint release part of a startup reservation early,
  or is the declared startup window cheap enough to keep the first version simpler?
- What exact adapter-version and working-directory identity is sufficient for session
  calibration without creating a high-cardinality persistent cache?
- At what validated boundary should catastrophic sentinel termination become default
  rather than opt-in?
- Should the authoritative launch handshake remain a short-lived wrapper that inherits
  secrets and file descriptors, or can a broker-spawn API meet Metaproc’s logging and
  credential-isolation contracts without widening the trusted state surface?
- Which project owns releases and protocol support for the standalone runtime, and how
  long must Metaproc support an older broker during rolling upgrades?
- Which current RunPool configuration and event fields are generic enough to move, and
  which remain Metaproc projections or compatibility shims?
- Where cgroup delegation exists, should one cgroup contain each run, each execution
  profile, or the whole local Metaproc host namespace?
  The answer affects both fairness and what one OOM event is allowed to kill.

## References

- [RunPool architecture](../../../../src/metaproc/docs/arch-runpool.md)
- [Process framework theory: readiness versus admission](../../../../src/metaproc/docs/process-framework-theory.md#resources-readiness-versus-admission)
- [RunPool design backlog](../../design/backlog/arch-runpool-backlog.md)
- [Standalone macOS memory guard](https://gist.github.com/jlevy/5b43e0d44166b9c7fe8157ee938cb0d5)
- [GNU Parallel memory and launch controls](https://www.gnu.org/software/parallel/parallel.html)
- [Apple Activity Monitor memory accounting](https://support.apple.com/guide/activity-monitor/view-memory-usage-actmntr1004/mac)
- [XNU memorystatus notifications](https://github.com/apple-oss-distributions/xnu/blob/main/doc/vm/memorystatus_notify.md)
- [Linux Pressure Stall Information](https://docs.kernel.org/accounting/psi.html)
- [Linux cgroup v2 memory controller](https://docs.kernel.org/admin-guide/cgroup-v2.html#memory)

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
