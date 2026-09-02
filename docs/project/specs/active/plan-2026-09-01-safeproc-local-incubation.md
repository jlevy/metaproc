---
title: Safeproc Local Incubation
description: Incubate an independently buildable process-safety package under packages/safeproc, prove its CLI and Python boundaries, and prepare extraction without adding a standalone pool.
author: Joshua Levy (github.com/jlevy) with LLM assistance
date: 2026-09-01
last_updated: 2026-09-02
status: Draft
category: plan
tracking_bead: mp-bd6v
---
# Feature: Safeproc Local Incubation

**Date:** 2026-09-01 (last updated 2026-09-02)

**Author:** Joshua Levy (github.com/jlevy) with LLM assistance

**Status:** Draft; this pull request changes plans and research only.
Revised on 2026-09-02 against the review tracked as `mp-sbue`, with each finding’s
disposition under `mp-yajq`.

## Overview

Create `packages/safeproc` in this repository on a later implementation branch, prove it
as an independently buildable Python distribution, and move it to a dedicated repository
only after its process-safety boundary has operating evidence.

Safeproc should be more than the current macOS memory-guard script and less than
Metaproc. It should provide:

- a brokerless monitor for an existing process tree;
- an owned launch path that establishes admission and containment before the target
  executes;
- one normalized macOS and Linux safety core;
- a small per-user broker and sentinel for host-wide claims, launch pacing, embargoes,
  and last-resort containment;
- deterministic journals and replay;
- a typed Python library and thin `safeproc` command-line interface over the same
  implementation.

It should not provide a submission queue, retry engine, workflow model, durable job
service, `SafeRunPool`, or `safeproc pool` command in its first release.
Metaproc keeps its current RunPool and builds on Safeproc’s owned-process boundary.
A pool extraction remains a later, separate go/no-go decision.

This branch does not create the package, alter the uv workspace, change dependencies, or
modify runtime code.
Its deliverable is the implementation map, consolidated research, and a tbd task graph.
Keeping that boundary lets the current design pull request merge before package
implementation begins.

Two plans describe this work, and each owns one job.
The [RunPool Host Safety Envelope](plan-2026-09-01-runpool-host-safety.md) owns policy:
safety invariants, the resource model, the pressure state machine, shedding and
containment rules, the sentinel, platform evidence requirements, Metaproc integration,
and rollout gates.
This plan owns the package: layers and import direction, public Python
types, the CLI, the platform capability table, the launch primitive, quality gates, uv
workspace mechanics, extraction, and the phase list that the tbd beads implement.
Where both could describe the same thing, the owner’s text is normative and the other
links to it. The phase numbering in this plan is canonical.

## Decision

The package boundary is worth pursuing, with two constraints:

1. Safeproc owns safety for one process tree and host-wide coordination among those
   trees. It does not own work submission or workflow scheduling.
2. Local incubation must preserve independent build and test behavior from the first
   code commit. Code may live in the Metaproc repository temporarily, but it may not
   import Metaproc or rely on Metaproc artifacts.

The CLI and Python surfaces are not separate products.
They are adapters over the same typed requests, services, policy engine, platform
providers, and journal records:

| Consumer | Surface | Why it exists |
| --- | --- | --- |
| Shell scripts and operators | `safeproc watch`, `run`, `status`, and `replay` | Protect or profile work without importing Python or installing Metaproc |
| Python launchers | `ProcessMonitor` and `SafeProcess` | Compose lifecycle and events without parsing CLI output |
| Metaproc | `SafeProcess` plus typed events | Retain one scheduler while delegating launch safety and host coordination |

This split becomes a bad abstraction if Safeproc gains a second queue, Metaproc-specific
artifact paths, adapter policy, or a release requirement for ordinary RunPool changes.
Those are stop conditions, not future cleanup items.

## Goals

- Produce a useful standalone package before extracting a standalone pool.
- Keep brokerless monitoring usable for arbitrary existing process trees.
- Make owned launch the authoritative path for pre-execution admission, process-group
  identity, cancellation, and cleanup.
- Share one policy, identity model, journal, replay engine, and platform evidence model
  across monitored and owned modes.
- Support macOS and Linux as tested, first-class platforms.
- Keep the first runtime dependency set empty unless measured evidence proves that a
  dependency is safer than a small standard-library implementation.
- Make the package independently lintable, type-checkable, testable, buildable, and
  wheel-smokeable while it lives under `packages/safeproc`.
- Raise the quality bar for failure-state code without making unrelated Metaproc
  development run Safeproc’s slow platform or stress suites.
- Preserve one root lockfile and one supply-chain policy during incubation.
- Make later history-preserving extraction mechanical and auditable.
- Prevent an unpublished workspace package from leaking into a released Metaproc wheel.

## Non-Goals

- Implement any package or runtime behavior in this planning pull request.
- Add a standalone pool, work queue, retry policy, lane system, DAG, or artifact model.
- Move `metaproc.runpool` wholesale into `packages/safeproc`.
- Publish Safeproc from the Metaproc repository.
- Make Metaproc depend at runtime on a workspace-only distribution.
- Preserve a public Safeproc API before its first external release.
- Promise Windows support in the first release.
  Windows is deferred, not declined; the system plan records the decision and the
  starting capability record, and bead `mp-4ksz` writes the provider design after the
  macOS and Linux providers exist.
- Copy Procguard source.
  Its edge cases inform contract tests; its implementation and formal claims are not
  Safeproc provenance.
- Treat the existing memory-guard script as a frozen API. Its evidence, policy cases,
  and replay corpus are inputs to a cleaner cross-platform design.
- Replace adapter-specific demand reduction.
  Safeproc protects the host after known causes such as Gemini’s session scan have been
  mitigated where possible.

## Consolidated Research Boundary

Metaproc should retain two canonical research records for this work:

- [Agent CLI Startup Memory](../../research/research-2026-09-01-agent-cli-memory-usage.md)
  owns measured client behavior, including Gemini’s project-history transient and the
  cross-client controls.
- [Host Memory Accounting and Control](../../research/research-2026-09-01-host-memory-accounting-and-control.md)
  owns macOS and Linux gauges, process-tree attribution, admission, launch pacing, and
  emergency-containment semantics.

The gauge citations themselves, with the kernel sources and reproduction commands, live
in the repository’s
[memory accounting reference](../../../memory-accounting-reference.md); the research
record owns the control model and links there for the numbers.

Recent downstream research has been adapted into those records.
Consumer-specific paths, issue identifiers, operational receipts, pipeline runbooks, and
deployment tasks remain downstream.
The standalone macOS guard plan also remains an implementation record in its source
project; Metaproc carries the reusable findings rather than a second copy of that plan.

The resulting documentation hierarchy is:

```text
measured client behavior ─┐
                          ├── RunPool Host Safety Envelope ── Metaproc rollout
host accounting/control ──┘                 │
                                            └── Safeproc Local Incubation
                                                package, gates, extraction
```

## Project and Template Baseline

### Adoption Mode

Safeproc begins as a package inside an existing repository, so it should use a
**selective core-template adaptation**, not pretend to be a second complete
simple-modern-uv repository.

The latest simple-modern-uv `main` checked for this plan was commit
`019733b9f0806afee542af9076ccf891e3fc5293`, tagged `v0.5.0`. That is also the template
version recorded by Metaproc’s root `.copier-answers.yml`. The user-authorized
no-cool-off exception applied only to fetching and inspecting this first-party template
revision for the plan.
It does not waive the repository’s dependency cool-off or audit policy.

During incubation:

- do not add `packages/safeproc/.copier-answers.yml`;
- adapt the template’s package metadata, Hatchling build, uv commands, Ruff,
  BasedPyright, pytest, distribution checks, and supply-chain conventions;
- let the repository root continue to own GitHub workflows, hooks, `uv.toml`, the
  lockfile, and shared development tools;
- record deviations in this plan and package documentation rather than claiming a full
  nested template render.

After extraction, render the then-current full simple-modern-uv template into the new
repository, reconcile Safeproc into that render, and create an honest root
`.copier-answers.yml`. The extracted repository then becomes fully template-managed.

### Initial Project Answers

| Template field | Incubation answer | Rationale |
| --- | --- | --- |
| Distribution | `safeproc` | Working standalone and PyPI name |
| Import package | `safeproc` | No translation layer between docs, CLI, and Python |
| CLI | `safeproc` | One command with subcommands; no separate product name |
| Description | Cross-platform process monitoring, owned launch, and host-safety coordination for macOS and Linux | Names the actual boundary |
| Python | `>=3.12,<4.0` | Matches Metaproc and its CI matrix during incubation |
| Publication | Disabled | The nested package is not published from this repository |
| Classifier | `Private :: Do Not Upload` during incubation | Makes accidental upload fail visibly |
| License | Repository license pending an explicit extraction decision | Do not silently relicense guard-derived or incubated code |

As of the 2026-09-01 planning check, the PyPI project page for `safeproc` returned 404
and `github.com/jlevy/safeproc` did not exist.
These are point-in-time observations, not reservations.
Recheck both immediately before repository creation or publication.

## Package Architecture

### Dependency Direction

Safeproc has concentric layers with enforceable one-way imports:

```text
safeproc CLI adapters
        │
        ├── watch ── ProcessMonitor ───────────────┐
        ├── run ─── SafeProcess ── broker client ─┤
        ├── status ──────────────── broker client ┤
        └── replay ── journal replay ─────────────┤
                                                   │
                              policy, identity, records, clocks
                                                   │
                                      macOS and Linux providers
```

The policy, identity, records, replay, clock, and platform contracts may not import the
CLI, broker, owned-process service, or Metaproc.
`ProcessMonitor` may depend on that core but not on the broker or owned-process service.
`SafeProcess` may depend on the broker client and shared core.
No module in the distribution may import `metaproc`.

### Proposed Layout

The first implementation should start with this shape and split modules only when a
layer has distinct invariants or platform ownership:

```text
packages/safeproc/
├── pyproject.toml
├── README.md
├── CHANGELOG.md
├── LICENSE
├── docs/
│   ├── architecture.md
│   ├── cli.md
│   └── protocol.md
├── src/safeproc/
│   ├── __init__.py
│   ├── py.typed
│   ├── cli.py
│   ├── clocks.py
│   ├── identity.py
│   ├── journal.py
│   ├── models.py
│   ├── monitor.py
│   ├── owned.py
│   ├── policy.py
│   ├── replay.py
│   ├── _broker/
│   │   ├── client.py
│   │   ├── protocol.py
│   │   ├── sentinel.py
│   │   └── service.py
│   └── _platform/
│       ├── base.py
│       ├── darwin.py
│       └── linux.py
└── tests/
    ├── unit/
    ├── contract/
    ├── integration/
    └── fixtures/
```

Only supported types are re-exported from `safeproc.__init__`. The `_broker` and
`_platform` packages are internal.
Platform interfaces and broker messages remain testable, but consumers do not import
their implementations.

### Python Surface

The initial public design has two distinct lifecycle APIs:

| Type | Responsibility |
| --- | --- |
| `ProcessTarget` | PID plus creation identity and optional observation labels for an existing process |
| `ProcessMonitor` | Validate a target, observe its changing descendant tree, apply an explicit monitored-process policy, and write a journal |
| `MonitoredProcess` | Handle for samples, events, wait, and stop-monitoring operations; the name states monitoring rather than operating-system attachment |
| `SafeProcessRequest` | Command, environment and descriptor policy, working directory, resource profile, clocks, and cleanup contract for owned launch |
| `SafeProcess` | Admit, launch into an isolated process group, register identity, wait, cancel, and clean up |
| `SafeProcessResult` | Distinguish workload exit, timeout, cancellation, external signal, host-pressure preemption, and supervisor failure |
| `ResourceProfile` | Startup peak and window, steady cost, launch spacing, reserve behavior, and measurement identity |
| `HostSample` and `PressureDecision` | Normalized evidence and pure policy output with explicit platform and metric scopes |
| `JournalRecord` | Versioned, redacted record used by both live modes and replay |

Owned and monitored requests must not be one structure with a boolean mode.
Their safe actions differ.
Monitoring is passive by default and cannot claim admission or process group ownership
after the fact. Owned launch establishes admission, session and process group identity,
and cleanup authority before the target command executes.

Async operations are the primary Python surface because Metaproc already owns an event
loop and many safe processes must coexist in one interpreter.
A narrow synchronous convenience function may be added for single-process scripts only
if it is a wrapper around the async service and does not install global signal handlers.

### Command-Line Surface

The package exposes one `safeproc` command:

```text
safeproc watch --pid PID [--policy observe|guard] [--journal PATH]
safeproc run --profile PROFILE [--journal PATH] -- COMMAND [ARG ...]
safeproc status [--format text|json]
safeproc replay JOURNAL [--format text|json]
```

- `watch` is brokerless and observation-only unless the operator names an intervention
  policy explicitly.
- `run` uses the broker for an authoritative host claim unless the operator selects an
  explicit unsafe development mode.
- `status` inspects broker identity, claims, waiters, embargoes, and incidents without
  changing them.
- `replay` is offline and deterministic.
- diagnostics go to stderr; machine output goes to stdout; CI disables progress; exit
  codes are documented and stable after the first external release.

Use `argparse` in the first version.
Typer, Rich, Pydantic, psutil, and similar dependencies are useful elsewhere in Metaproc
but do not justify enlarging a rescue path that must import and start under host
pressure.

### Platform Boundary

The shared core consumes capabilities rather than platform guesses:

| Capability | macOS | Linux |
| --- | --- | --- |
| Host admission budget | `host_statistics64` free, inactive, and purgeable pages | the smaller of `/proc/meminfo` `MemAvailable` and the caller’s own cgroup headroom, `memory.max` minus `memory.current` |
| Measured danger | `kern.memorystatus_vm_pressure_level` 4; reclaimable below the floor under pressure; swap-volume suspension distance below its line; red-line ratio under pressure | sustained PSI `full`; reclaimable below the floor |
| Predictive warning | compressor growth, reclaimable slope, projected time to floor | PSI `some` rising, `MemAvailable` slope, swap-in rate from `/proc/vmstat` |
| PSI capability | not applicable | three states: absent; readable averages; pollable triggers (`CAP_SYS_RESOURCE` before 6.5, unprivileged two-second multiples since); cgroup-local `memory.pressure` preferred |
| Process cost | complete-tree `phys_footprint` via `proc_pid_rusage`, about 0.2 ms for a 60-process tree | cgroup `memory.current` in constant time; complete-tree PSS from `smaps_rollup` behind an accuracy gate, because it walks page tables |
| Identity | PID plus creation time from native process APIs | `pidfd_open` on 5.3 and later; PID plus `/proc/<pid>/stat` start time otherwise |
| Owned containment | new session and process group | new session and process group; `PR_SET_CHILD_SUBREAPER` on the wrapper; delegated cgroup v2 with `cgroup.kill` on 5.14 and later when available, via `systemd-run --user --scope` on systemd hosts |
| Kernel safety net | none; `CONFIG_JETSAM` is not compiled in | OOM killer; leaves launch with raised `oom_score_adj`, and an OOM kill is reconciled as a preemption |
| Existing tree | repeated identity-fenced descendant discovery | repeated identity-fenced descendant discovery |
| Sleep-aware clock | `mach_continuous_time`; active time from the monotonic clock | `CLOCK_BOOTTIME`; active time from `CLOCK_MONOTONIC` |
| Sentinel scheduling | `THREAD_PRECEDENCE_POLICY` 63 plus timeshare off | none available unprivileged; rely on not forking |
| Exit observation | `kqueue` `EVFILT_PROC` with `NOTE_EXIT` | `pidfd` readable on exit |

Windows is not a column because it is not a first-release platform; the system plan
records the deferral and its starting capability record.

Unsupported evidence is explicit in the capability record.
A backend may expose an RSS fallback for diagnostics but may not label it footprint or
PSS. The core must know whether evidence is root, tree, cgroup, or host scoped.

The authoritative launch and sampling path must not fork a memory-heavy Python parent or
spawn helper commands.
Use `posix_spawn`-class launch, a minimal child wrapper when pre-exec setup is
unavoidable, `ctypes` for stable macOS APIs, and procfs on Linux.

### Launch Primitive

CPython’s `subprocess` cannot provide the nonforking launch the system plan requires.
In Python 3.12, `subprocess.py` uses `posix_spawn` only when there is no `preexec_fn`,
`close_fds` is false, there are no `pass_fds`, no `cwd`, stdio is not redirected to a
low descriptor, `start_new_session` is false, no process group is requested, no uid or
gid changes, and no umask.
Metaproc’s `LocalBackend` sets `start_new_session=True` and keeps the default
`close_fds=True`, so every launch today goes through `_posixsubprocess.fork_exec`; on
Linux that path can use `vfork`, and on macOS it is a `fork` of the parent.
`asyncio.create_subprocess_exec` wraps the same `Popen`.

The owned launch path therefore uses four pieces, all standard library:

1. **The spawn call.** `os.posix_spawn` with `setsid=True`, which maps to
   `POSIX_SPAWN_SETSID` on macOS and glibc 2.26 and later, and with `setpgroup` where a
   group without a new session is wanted.
   It creates the isolated session and process group without forking the supervisor.
   It has no `close_fds`; descriptor hygiene comes from PEP 446, under which Python
   creates descriptors non-inheritable by default, plus explicit `file_actions` for the
   descriptors the child must receive.
   macOS `POSIX_SPAWN_CLOEXEC_DEFAULT` is not reachable from Python and is not relied
   on.
2. **Exit observation without `SIGCHLD`.** `asyncio` child watchers assume `Popen`. The
   owned path registers its own waiter: on Linux 5.3 and later, `pidfd_open` and the
   event loop’s reader; on macOS, `select.kqueue` with `EVFILT_PROC` and `NOTE_EXIT`;
   where neither exists, a waiter thread calling `waitpid`. The waiter is created before
   the spawn returns control to the caller, so a target that exits immediately is
   observed rather than lost.
3. **The wrapper handshake.** The spawned process is a minimal wrapper that obtains the
   host claim, creates the session and group if the spawn flags could not, records its
   PID, create time, and group with the broker, applies any per-child setup, and then
   `exec`s the target. The supervisor holds one end of a pipe that the wrapper closes on
   `exec`; the supervisor treats the launch as owned only when the broker has
   acknowledged the registration and the pipe has closed.
   If the wrapper dies between spawn and `exec`, the pipe closes without an
   acknowledgment, the supervisor reports a supervisor failure rather than a workload
   exit, and identity-based stale reclamation releases the reservation once it can prove
   that no target survived.
4. **Sampling.** The same rule forbids helper subprocesses on the sampling path.
   `host_statistics64` and `proc_pid_rusage` through `ctypes` replace `vm_stat` and
   `sysctl`; the guard measured them at 24 microseconds and 0.2 milliseconds for a
   60-process tree against 313 milliseconds for the helper commands.
   Metaproc’s existing `osutils/memory_pressure.py` is replaced by the provider, not
   wrapped by it.

Bead `mp-t9u5` proves these four pieces together on macOS and Linux under `asyncio`
before the broker and owned launch are built; `mp-3c0g` depends on it.

### Broker and Sentinel

The first authoritative client elects one per-user broker for one host namespace.
The broker owns:

- serialized resource claims and count caps;
- launch-spacing deadlines across independent clients;
- process identities and conservative stale-claim reclamation;
- host pressure state and launch embargoes;
- one-responder incident pacing;
- catastrophic containment for registered owned groups only;
- a bounded idle shutdown.

It does not receive prompts, credentials, complete environments, file descriptors,
Metaproc paths, or retry policy.
A short-lived launch wrapper inherits sensitive launch state directly from the caller
while the broker receives only redacted labels, resource claims, and process identities.

The protocol and journal are versioned from the first persisted artifact.
Unknown major versions fail closed for authoritative launches.
Minor additions are optional and additive.
Deadlines name their clock domain, and process liveness uses identity rather than PID
alone. A system sleep must not make a live process’s startup claim appear expired.

## Repository Incubation

### uv Workspace

The implementation branch converts the repository root into a uv workspace:

```toml
[tool.uv.workspace]
members = ["packages/*"]
```

There remains one root and one `uv.lock`. Do not add a package-local lockfile while
Safeproc is a workspace member.
Run package commands with uv’s package selection, for example
`uv run --package safeproc ...` and `uv build --package safeproc --no-sources`.

Do not add Safeproc to Metaproc’s runtime dependencies during foundation work.
A root `[tool.uv.sources]` workspace source becomes appropriate only with a deliberate
local integration dependency.
Even then, the source-free Metaproc build gate must prove that no released wheel
requires an unpublished workspace project.

The safest rollout is:

1. build and test Safeproc independently as a workspace member;
2. run a root-owned contract harness with both projects installed for shadow
   integration;
3. extract and publish Safeproc `0.x`;
4. add the released distribution to Metaproc through the normal dependency and
   supply-chain process;
5. only then ship a Metaproc wheel that imports Safeproc in production.

### Build and Versioning

The nested project uses Hatchling and uv-dynamic-versioning, matching the template and
Metaproc.
During incubation, version tags must not collide with Metaproc’s `vX.Y.Z` tags.
Configure a `safeproc-` pattern prefix so package tags are `safeproc-vX.Y.Z`, with a
clearly non-release fallback for untagged local builds.

The package build gate is:

```text
uv build --package safeproc --no-sources
```

`--no-sources` proves that workspace-only source overrides are not required for the
distribution.
Inspect the sdist and wheel, install the wheel into an isolated environment
with the source tree unavailable, import `safeproc`, run `safeproc --help`, and execute
brokerless `watch` and replay smoke tests.

The build gate runs in CI on every pull request that touches `packages/`, not only at
handoff, so a workspace-only source leak is caught when it is introduced rather than at
release time.

No publish job accepts `safeproc-v*` tags in the Metaproc repository.
On extraction, choose whether to retain the prefixed tag history or reset the
unpublished package to the new repository’s ordinary `vX.Y.Z` release convention before
the first public tag.

### Root Commands

The implementation adds targeted root targets rather than copying a nested Makefile:

```text
safeproc-format
safeproc-lint-check
safeproc-test
safeproc-build
verify-safeproc
```

`make verify` includes `verify-safeproc`. The targeted commands pass the nested
`pyproject.toml` explicitly to Ruff, BasedPyright, and pytest so the package keeps an
extractable configuration.
Unrelated Metaproc edit loops may run the existing focused commands; the complete
handoff and CI gate always includes Safeproc.

## Quality Bar

### Static Analysis

- Keep the root Ruff floor and add high-value rules for async, path, exception, and
  simplification mistakes after a zero-warning spike.
  Do not enable `ALL` and spend the project on stylistic waivers.
- Run BasedPyright in strict mode over `src/safeproc`. Do not disable the unknown and
  `Any` families globally for Safeproc.
  Boundary parsing narrows untrusted JSON or platform data immediately.
- Require complete annotations in source and tests where fixtures cross process or
  protocol boundaries.
- Enforce import direction and prove that importing `safeproc.monitor` does not load
  broker, owned-process, CLI, or Metaproc modules.
- Check that the runtime dependency list is empty and that no subprocess helper appears
  on the authoritative sampling path.

Higher bars are useful when they catch safety defects.
They should remain package-local and evidence-based so they do not slow unrelated
Metaproc work or create broad waiver files.

### Deterministic Tests

Use injected clocks, process tables, platform providers, spawn primitives, signal sinks,
filesystem stores, and broker transports.
Ordinary tests contain no unbounded sleeps and use bounded deadlines for every
subprocess.

Require complete branch coverage for the pure policy, identity, protocol, and replay
state-machine modules.
Do not impose a vanity 100 percent threshold on platform syscall wrappers.
Their acceptance comes from contract, failure-injection, and live smoke tests.

The deterministic corpus includes:

- target exits before first observation;
- PID reuse between discovery, sampling, and signalling;
- descendant exits while the tree is enumerated;
- immediate exit and zero timeout;
- `ESRCH`, access denied, and partial process tables;
- process-group creation and children-before-parent cleanup;
- stopped or suspended targets and guaranteed resume cleanup;
- system sleep across monotonic, boot-time, and wall-clock deadlines;
- broker crash before spawn, during registration, and after target launch;
- client crash with and without a surviving owned process;
- stale or corrupt claim files and incompatible protocol versions;
- missing host gauges, late samples, clock regression, and journal write failure;
- simultaneous pressure responders and settle-window enforcement;
- outside-tree pressure where killing the registered tree cannot recover the host;
- monitored mode refusing owned-only actions;
- zombies present in the tree, excluded from cost, victims, and grace waits;
- a producer pause reaching its cap while danger persists, the minimum service window
  before the next pause, and spawners born during a pause frozen on the next sample;
- a critical platform alarm with adequate headroom, which must not read as recovered;
- shedding rounds exhausted with the host short of the failure boundary, which must hold
  rather than abort;
- a monitored victim root that forks during enumeration, which the pre-enumeration stop
  must defeat;
- an OOM kill on Linux reconciled as a host-pressure preemption;
- each PSI capability state, and a memory-limited cgroup whose headroom is below host
  `MemAvailable`;
- a `pidfd` outliving a recycled PID, and subreaper reparenting of an orphaned
  grandchild;
- replay producing the same decision sequence as live policy evaluation.

Procguard’s immediate-exit, `ESRCH`, process-group, zero-timeout, sleep-aware-clock, and
suspended-process cases should be translated into implementation-independent contract
tests with provenance recorded in test comments.
Do not copy its source or claim its tests prove Safeproc.

### Platform and Packaging Matrix

CI for Safeproc covers:

| Gate | Platforms | Python |
| --- | --- | --- |
| Format, lint, type, unit, deterministic contract | Ubuntu | 3.13 unless a version-specific issue requires the matrix |
| Package tests and wheel smoke | Ubuntu and macOS | 3.12, 3.13, and 3.14 |
| Native provider integration | Ubuntu and macOS | 3.13 |
| Broker/process race suite | Ubuntu and macOS | 3.13 |
| Scaled pressure and destructive containment | Opt-in or scheduled dedicated hosts | One supported version per platform |

Normal pull requests do not allocate enough memory to pressure a shared CI host and do
not signal unrelated processes.
Scaled providers and replay test policy cheaply; dedicated opt-in tests validate live
containment with explicit ownership.

## Supply Chain, Licensing, and Publication

- The repository’s 14-day dependency cool-off, hash and action pinning, vulnerability
  audits, and public-hygiene checks apply throughout incubation.
- The first runtime dependency set is empty.
  Development and build tools use the shared root lock.
- The planning exception for the latest first-party simple-modern-uv template does not
  extend to package dependencies or third-party source.
- Keep a provenance record for code, test cases, and replay fixtures derived from the
  memory guard. Translate Procguard behaviors independently and record only the public
  version reviewed.
- The replay corpus is the guard’s journals, nineteen runs from one downstream
  calibration host. It enters this repository only through a sanitizing export that the
  guard’s author runs at the source: hostnames, user names, absolute paths, argv,
  environment values, and process command lines are dropped or replaced with stable
  labels, PIDs are remapped, and numeric samples, timings, pressure levels, and events
  are kept. The result lives under `packages/safeproc/tests/fixtures/replay/` with a
  `PROVENANCE.md` naming the export script and date.
  A sanitized numeric journal is a test fixture, not a copied operational artifact, and
  it must pass the public-hygiene check like any other file; the repository rule against
  operational artifacts is about the unsanitized originals, which stay downstream.
- While Safeproc lives here, do not declare a license inconsistent with the repository
  without an explicit copyright-holder decision.
  Before extraction, choose whether the standalone project retains AGPL-3.0-or-later or
  receives a deliberate permissive license, and verify that every derived artifact can
  be distributed under that choice.
- Keep `Private :: Do Not Upload`, omit trusted-publishing configuration, and make any
  accidental nested publication fail during incubation.
- The new repository must run full verification at the exact release commit, use PyPI
  trusted publishing, and inspect source and wheel contents before the first `0.x`
  release.

## Metaproc Boundary

Metaproc retains:

- submission ordering, queueing, concurrency targets, lanes, retries, and drain;
- process specs, execution profiles, adapter policy, and provider backoff;
- log files, invocation sidecars, trace events, status projections, and run artifacts;
- credential scrubbing and adapter-specific environment construction.

Safeproc owns:

- resource claims and host-wide launch spacing;
- owned process-group lifecycle and generic cancellation;
- monitored process-tree discovery and identity fencing;
- host and tree measurement, pressure policy, and journal records;
- broker election, embargoes, incidents, and registered-group containment.

The integration adapter translates a Metaproc execution profile into a neutral
`ResourceProfile`, calls `SafeProcess`, and projects generic lifecycle records into
Metaproc artifacts. Safeproc never writes `runpool-status.yaml`, Metaproc trace events,
or run directories.

The first shadow harness is root-owned and may install both workspace projects for a
test. Safeproc’s own test dependency graph and distribution remain Metaproc-free.
A released Metaproc wheel does not import Safeproc until an external Safeproc release
has passed the normal dependency gate.

## Implementation Plan

These phases are the canonical numbering for the work and the order of the tbd beads.
The system plan’s integration stages begin after Phase 3 and are named, not numbered.

### Phase 0: Land the Design and Research

- [x] Consolidate agent startup-memory and host-accounting research in Metaproc.
- [x] Choose `MonitoredProcess` rather than `AttachedProcess` for an existing target.
- [x] Decide that owned and monitored modes share a core but retain distinct authority.
- [x] Defer every pool API and extraction decision.
- [x] Inspect the latest simple-modern-uv template and select local adaptation mode.
- [x] Specify the uv workspace, package tree, quality gates, and extraction boundary.
- [ ] Merge this docs-only pull request before beginning package implementation.

### Phase 1: Workspace and Pure Core

- [ ] Add `packages/safeproc` as a uv workspace member with independent metadata, `src`
  layout, typed marker, documentation, tests, and no runtime dependencies.
- [ ] Add the package-specific Ruff, strict BasedPyright, pytest, build, distribution,
  and import-boundary configuration.
- [ ] Add targeted root Make and CI gates; keep one root lockfile and supply-chain
  policy.
- [ ] Implement neutral clocks, identities, samples, resource profiles, decisions,
  actions, results, and versioned journal records.
- [ ] Implement pure admission, pressure, settle, and intervention state machines plus
  deterministic replay.
- [ ] Import the sanitized memory-guard replay corpus, exported as described under
  Supply Chain, and independently translated Procguard contract cases, each with
  provenance.

### Phase 2: Brokerless Monitoring on macOS and Linux

- [ ] Implement native macOS and procfs Linux providers behind one capability contract.
- [ ] Implement identity-fenced descendant discovery and complete-tree accounting.
- [ ] Implement `ProcessMonitor` and `MonitoredProcess`; observation is the default and
  intervention authority is explicit.
- [ ] Implement `safeproc watch` and `safeproc replay` with no broker imports or
  startup.
- [ ] Prove passive profiling, process exit, PID reuse, sleep, sampling starvation,
  compression, PSI, and outside-tree-pressure behavior.
- [ ] Build and smoke the source and wheel distributions on macOS and Linux.

This phase must already produce a useful standalone side guard.
If it cannot, the package boundary is too coupled and extraction should stop.

### Phase 3: Owned Launch and Host Broker

- [ ] Complete the launch-primitive spike (`mp-t9u5`): `os.posix_spawn` with a new
  session, a stdlib exit waiter, descriptor hygiene, and the wrapper handshake, on both
  platforms under `asyncio`.
- [ ] Implement the versioned broker protocol, per-user election, identity-fenced stale
  recovery, claim registry, launch pacing, embargo, and idle exit.
- [ ] Implement a nonforking owned launch wrapper that establishes admission and an
  isolated process group before target execution.
- [ ] Implement `SafeProcess`, `SafeProcessResult`, `safeproc run`, and
  `safeproc status`.
- [ ] Prove cleanup or visible ownership after every injected client, wrapper, broker,
  spawn, registration, and journal failure.
- [ ] Run one journal and policy conformance suite through monitored and owned modes.
- [ ] Keep catastrophic containment disabled by default until replay and live smoke show
  zero destructive false positives.

### Phase 4: Metaproc Shadow Contract and Extraction Readiness

- [ ] Add a root-owned shadow harness that translates representative Metaproc profiles,
  compares Safeproc claim decisions with current RunPool decisions, and imports neither
  project through private modules.
- [ ] Confirm that scheduler-only Metaproc changes require no Safeproc release and
  platform-safety changes require no RunPool redesign.
- [ ] Resolve the standalone license and complete the provenance audit.
- [ ] Recheck the package and repository names.
- [ ] Prove `uv build --package safeproc --no-sources`, isolated wheel operation,
  brokerless `watch`, version-skew failures, secret redaction, and zero Metaproc
  imports.
- [ ] Rehearse `git subtree split --prefix packages/safeproc` and verify that the split
  history builds independently.
- [ ] Record a go/no-go extraction decision.

### Phase 5: Separate Repository and Metaproc Adoption

This phase begins only after a separate approval to create or publish the repository.

- [ ] Render the then-current complete simple-modern-uv template into the new repository
  and reconcile the history-preserving Safeproc tree into it.
- [ ] Establish honest Copier lineage, repository-level CI, release, security, and
  contribution files.
- [ ] Publish an experimental `0.x` release only after complete macOS and Linux gates.
- [ ] Add the released package to Metaproc through the normal cool-off, lock, audit, and
  installed-wheel gates.
- [ ] Integrate `SafeProcess` beneath Metaproc’s retained RunPool in shadow mode, then
  promote claims and pacing through staged rollout.
- [ ] Remove superseded Metaproc lifecycle and telemetry code only after parity; keep
  the queue and adaptive controller.

### Deferred: Pool Extraction

Do not begin a pool spike until Safeproc has an external versioned release and Metaproc
has operated through `SafeProcess`. The later decision asks whether neutral submission,
cancellation, adaptive capacity, and drain can move without Metaproc paths, lanes,
events, provider policy, or coordinated release friction.
A no-go result is acceptable.

## tbd Task Graph

Epic `mp-bd6v` owns the documentation work and future implementation for this plan.
The implementation beads preserve the phase order without forcing the macOS and Linux or
monitored and owned work into one oversized change:

| Bead | Work | Depends on |
| --- | --- | --- |
| `mp-bu84` | Incubate the uv workspace and quality gates | Plan merge |
| `mp-lsve` | Implement safety models, policy, journal, and replay | `mp-bu84` |
| `mp-3i22` | Implement native macOS and Linux providers | `mp-lsve` |
| `mp-je1b` | Ship brokerless `ProcessMonitor`, `watch`, and replay | `mp-3i22` |
| `mp-t9u5` | Spike the nonforking launch primitive under `asyncio` | none; may run early |
| `mp-3c0g` | Implement broker, sentinel, and owned launch | `mp-3i22`, `mp-t9u5` |
| `mp-sfc0` | Calibrate Linux defaults on a dedicated host | none; before Linux defaults ship |
| `mp-c225` | Complete cross-platform safety and distribution gates | `mp-je1b`, `mp-3c0g`, `mp-sfc0` |
| `mp-4ksz` | Write the Windows capability record and provider design | `mp-3i22`; no first-release promise |
| `mp-v0ka` | Gemini adapter honors or rejects `no_session_persistence` | none; independent of Safeproc |
| `mp-0e2g` | Prove the Metaproc shadow contract and extraction readiness | `mp-c225` |
| `mp-mlet` | Extract and publish the first standalone release | `mp-0e2g`; explicit approval |
| `mp-g3si` | Adopt the released package beneath Metaproc’s RunPool | `mp-mlet` |
| `mp-rcct` | Evaluate pool extraction | `mp-g3si` plus operating evidence |

## Acceptance Criteria

- The planning pull request contains no package, dependency, workspace, CI, Makefile, or
  runtime-code changes.
- The first implementation branch produces an independently buildable
  `packages/safeproc` distribution with no imports from Metaproc.
- `safeproc watch` observes an existing tree with no broker and sends no signals by
  default.
- `SafeProcess` obtains an authoritative host claim and establishes isolated identity
  before the target executes.
- macOS and Linux use platform-correct host budgets, process costs, and degradation
  evidence with explicit scopes.
- The authoritative hot path uses no helper subprocesses and has no third-party runtime
  dependency.
- Deterministic replay reproduces live policy decisions, and owned and monitored modes
  share journal vocabulary while enforcing different authorities.
- PID reuse, immediate exit, zero timeout, sleep, suspended cleanup, protocol skew,
  broker crash, and partial process tables have explicit tests.
- Package gates pass on Python 3.12 through 3.14 and on macOS and Linux.
- `uv build --package safeproc --no-sources` and isolated installed-wheel smoke pass.
- No Safeproc distribution can be published from the Metaproc repository.
- No released Metaproc wheel depends on workspace-only Safeproc.
- License and provenance are resolved before external repository creation or
  publication.
- The first external release exposes no pool type, submission queue, or `pool` command.
- The subtree split builds and tests without the Metaproc checkout.

## Abstraction Review Gates

Proceed with extraction only if all answers remain yes:

1. Is brokerless `watch` useful to a caller that has never installed Metaproc?
2. Can `SafeProcess` launch and supervise arbitrary commands without Metaproc concepts?
3. Do CLI and Python calls execute the same services rather than duplicate policy?
4. Can Metaproc retain one queue and translate through public Safeproc types only?
5. Can a Safeproc platform fix ship without a Metaproc scheduler change?
6. Can a Metaproc lane, retry, or artifact change ship without a Safeproc release?
7. Does the package build from the split history with no workspace source override?
8. Are broker and journal compatibility costs justified by independent processes that
   must coordinate?

Stop or narrow the package if any of these occur:

- a second queue or retry state appears in Safeproc;
- Safeproc needs Metaproc paths, event classes, adapters, or credentials;
- `watch` requires the broker merely to observe;
- package-local gates make ordinary Metaproc iteration materially slower without
  catching cross-boundary defects;
- protocol evolution forces lockstep releases for changes that are not host safety;
- platform code cannot provide safer behavior than the existing in-process boundary.

## Open Questions

- Which license should the extracted project use, and which guard-derived artifacts are
  eligible under that license?
- Should the first external repository retain `safeproc-vX.Y.Z` tags or begin ordinary
  `vX.Y.Z` tags before its first release?
- Is zero runtime dependency a permanent rule or a first-release constraint subject to
  measurement?
- Which broker transport and state directory provide the best permission and stale
  recovery behavior on both platforms?
- Which destructive monitored-process policies, if any, should be public in `0.x` rather
  than retained as experimental internals?
- When cgroup v2 delegation exists, should an owned tree always receive a cgroup or only
  when a resource profile requests kernel enforcement?
- What operating evidence is enough to authorize the later pool-extraction spike?

## References

- [RunPool Host Safety Envelope](plan-2026-09-01-runpool-host-safety.md)
- [Agent CLI Startup Memory](../../research/research-2026-09-01-agent-cli-memory-usage.md)
- [Host Memory Accounting and Control](../../research/research-2026-09-01-host-memory-accounting-and-control.md)
- [simple-modern-uv v0.5.0](https://github.com/jlevy/simple-modern-uv/tree/v0.5.0)
- [uv workspaces](https://docs.astral.sh/uv/concepts/projects/workspaces/)
- [uv package builds](https://docs.astral.sh/uv/guides/package/)
- [uv-dynamic-versioning version sources](https://github.com/ninoseki/uv-dynamic-versioning/blob/main/docs/version_source.md)
- [Standalone macOS memory guard](https://gist.github.com/jlevy/5b43e0d44166b9c7fe8157ee938cb0d5)
- [Procguard v1.5.1](https://github.com/denispol/procguard/tree/v1.5.1)
- [Linux Pressure Stall Information](https://docs.kernel.org/accounting/psi.html)
- [Linux cgroup v2 memory controller](https://docs.kernel.org/admin-guide/cgroup-v2.html#memory)

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
