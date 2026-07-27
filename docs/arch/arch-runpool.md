---
title: "Architecture: RunPool"
description: Local agent process manager with adaptive concurrency and host coordination
author: metaproc team
status: Approved
---
# RunPool Design

**Date:** 2026-04-06 (last updated 2026-05-23) **Status:** Approved

> **Maintenance**: This is a maintained architecture doc.
> Revise via `tbd shortcut revise-architecture-doc` (which prompts you to verify content
> against current code, then add a “Future Considerations” section).
> When you make non-trivial changes, bump the **last updated** date above.
> The full arch-doc index lives in
> [development.md § Architecture docs](../development.md#architecture-docs).
> 
> Companion docs (in `metaproc/docs/`): [arch-metaproc-core](arch-metaproc-core.md),
> [arch-runpool](arch-runpool.md), [arch-cloud-execution](arch-cloud-execution.md),
> [arch-authentication](arch-authentication.md),
> [arch-claude-code-harness](arch-claude-code-harness.md),
> [arch-testing](arch-testing.md).

RunPool is Metaproc’s local agent process manager.
It owns subprocess lifecycle, adaptive concurrency, host-level coordination, health
telemetry, event logs, and kill coordination for local runs.
It does not own process DAG planning, prompt generation, domain workflows, item rosters,
auth policy, or result schemas.

Module-local docs are code navigation, not a second copy of the design.

## Design Goals

RunPool should let local Metaproc runs use available hardware without making the host
unusable. The target behavior is:

- use realistic concurrency ceilings instead of permanently low caps
- ramp up when host health is normal
- hold steady under moderate pressure
- reduce quickly under high or critical memory pressure
- make every resource decision visible in structured logs
- recover cleanly after sleep, killed orchestrators, or interrupted runs
- work on modern macOS and Linux hosts, and fail clearly on unsupported telemetry

Two issues must remain separate:

- **Memory safety:** do not drive the machine into active swap thrash or severe
  pressure.
- **Host coordination:** multiple RunPool parents on the same machine must not multiply
  local agent fan-out beyond the host’s intended ceiling.

Disk pressure is related but distinct.
Swap files and large logs can consume disk, but low disk is not itself memory pressure.
RunPool logs disk headroom and likely causes so operators can see whether swap growth,
active logs, or ordinary disk usage is involved.

## Boundary

RunPool owns:

- launching prepared subprocesses through a launch backend
- tracking active processes, descendants, RSS, logs, exits, stalls, timeouts, and kills
- adaptive concurrency for one pool
- host admission for aggregate local agent process slots
- system-health sampling for memory, swap growth, and disk headroom
- `runpool-status.yaml`, `events.jsonl`, and `health.jsonl` (full schemas in
  [artifact-catalog.md](../artifact-catalog.md))
- scale overrides and kill/drain sentinels

Metaproc orchestration owns:

- process DAG traversal and dependency resolution
- process-spec parsing and validation
- execution-profile selection
- adapter, prompt, auth, and environment construction
- retry policy and output validation
- cloud worker topology
- domain playbooks, item rosters, source-health policy, and result rollups

The interface should stay narrow: orchestration prepares `ProcessConfig` values, RunPool
returns `ProcessResult` values, and operator commands read structured status and event
streams.

## Current Telemetry

RunPool samples resource health on `pressure_check_interval_s` and records component
levels in both event and health logs:

| Component | Current source | Use |
| --- | --- | --- |
| macOS memory | `sysctl hw.memsize`, `kern.memorystatus_level` | Required memory headroom signal |
| macOS swap | `sysctl vm.swapusage` | Absolute swap visibility and swap-growth deltas |
| Linux memory | `/proc/meminfo` `MemTotal` and `MemAvailable` | Required memory headroom signal |
| Linux swap | `/proc/meminfo` `SwapTotal` and `SwapFree` | Absolute swap visibility and swap-growth deltas |
| Linux PSI | `/proc/pressure/memory`, when present | Optional stall-pressure refinement |
| Disk | `shutil.disk_usage()` for the run/log path | Diagnostic headroom and cause labeling |

Unsupported or broken required telemetry should be treated as a host setup error, not as
a reason to invent a safe-looking default.
Current code raises for unsupported OS families or missing required fields.
Windows is not supported.

### macOS

Apple documents Activity Monitor memory pressure as a composite of free memory, swap
rate, wired memory, and file cache.
That matches the design rule here: current memory pressure and active swap growth matter
more than absolute swap already allocated.

RunPool currently reads `kern.memorystatus_level` as a local CLI-friendly pressure proxy
and `vm.swapusage` for swap.
The app-level Apple API also exposes normal, warning, and critical memory-pressure
events through Dispatch memory-pressure sources; if `kern.memorystatus_level` stops
being reliable on supported macOS versions, the replacement should preserve the same
normal/elevated/high/critical contract and fail clearly when unavailable.

Operational rule: large `Swap Used` after a prior pressure event is not by itself proof
that the host is currently unsafe.
Positive swap growth over recent RunPool samples is the active pressure signal.

### Linux

RunPool uses `MemAvailable / MemTotal` from `/proc/meminfo` as the baseline memory
headroom signal. Linux man-pages describe `MemAvailable` as the estimate of memory
available for starting applications without swapping.

When `/proc/pressure/memory` exists, RunPool also reads PSI. Kernel PSI reports recent
stall percentages over 10, 60, and 300 second windows and is intended for dynamic
workload management under resource contention.
PSI should refine memory pressure; it does not replace required `MemAvailable` and swap
counters.

Future Linux work should add cgroup-aware readings for containerized workers when
Metaproc runs under cgroup limits that differ materially from host limits.

## Swap Policy

RunPool records two swap signals:

- `swap_used_gb`: absolute currently allocated swap
- `swap_delta_gb_per_min`: positive growth rate since the previous sample

The concurrency controller should react to active swap growth, not absolute swap alone.
Absolute swap is still useful context because:

- it explains why disk free space can be low after a pressure incident
- it helps operators decide whether a reboot or cleanup would reset the host baseline
- it provides a ceiling-risk signal when available disk is already scarce

Current swap-growth classification uses the growth rate as a fraction of physical RAM:

| Swap growth per minute | Level |
| --- | --- |
| `<= 0` or below 1 percent of RAM | `normal` |
| at least 1 percent of RAM | `elevated` |
| at least 3 percent of RAM | `high` |
| at least 10 percent of RAM | `critical` |

This keeps stale macOS swap allocation from permanently throttling runs while still
responding quickly when the host is actively paging.

## Disk Policy

RunPool logs disk free space and `disk_pressure_cause` for diagnosis:

- `swap_growth`: low disk while swap is actively growing
- `swap_reserve_high`: absolute swap is high enough to explain low free space
- `active_logs`: active agent logs are large enough to matter
- `low_disk_unknown`: low free space without a clear RunPool-local cause
- `none`: disk headroom is normal

Current status snapshots also expose an aggregate `level` for compatibility, and the
current adaptive controller still receives that aggregate level.
That means disk level can influence capacity today.
Operators should inspect `memory_level`, `swap_level`, and `disk_level` separately
before drawing conclusions.
A future behavior PR should keep disk cleanup decisions separate from memory-pressure
capacity decisions unless disk is so low that new process launches would be unsafe.

Log compaction and gzip compression are Metaproc log utilities, not RunPool scheduling
policy. RunPool should keep producing gzip-readable structured logs; compression should
remain safe to run ad hoc and safe to run automatically on inactive large logs.

## Adaptive Concurrency

RunPool uses a local adaptive semaphore.
The pool has a ceiling (`max_concurrency`), a current capacity, a memory ceiling, a
provider ceiling, and an operator cap.
Effective capacity is the most restrictive live ceiling.
The normal operating model is a reasonably high launch ceiling with automatic downshift
and recovery. A low operator cap is an explicit temporary intervention, not the default
safety model.

Startup concurrency defaults to a memory-budget estimate:

```text
initial = available_memory * initial_memory_budget_fraction
          / estimated_process_rss_bytes
```

Current pressure actions are applied to the aggregate health level today:

| Level | Action |
| --- | --- |
| `normal` | Ramp up after sustained healthy readings |
| `elevated` | Hold current capacity |
| `high` | Reduce memory ceiling by at least 25 percent |
| `critical` | Reduce memory ceiling by at least 50 percent |

Reductions are non-preemptive today: running subprocesses are not killed solely because
capacity falls below active count.
The cap takes effect as processes exit and slots are released.
Pressure-shedding, where RunPool kills low-priority active work under sustained critical
pressure, should be a separately designed and tested feature.

Provider pressure is separate from memory pressure.
Bursts of provider rate-limit failures reduce the provider ceiling, and provider
recovery can raise it again after clear samples.

Operator caps are an emergency brake and local testing tool.
They are useful when a live run is already harming host stability, but they should be
cleared after the incident with `uv run metaproc pool override <run-dir> --clear`.
Leaving a low override in run state pins future pools below the dynamic ceiling and
defeats adaptive recovery.

**Operator cap floor.** The operator cap is a hand-set ceiling, not the safety governor.
The memory and provider ceilings are what actively govern under pressure.
For local agent-pool dispatches (claude, codex, gemini, pi-cli) the operator cap should
be set high (≥20) and the adaptive controller left to ratchet down.
Setting the operator cap low to “be safe” silently caps
`effective_target = min(memory_ceiling, provider_ceiling, operator_cap)` even when the
adaptive controller would have allowed more, and the operator gets no warning —
wall-clock just stretches by 2-8×. Per-adapter memory profiles still shift with CLI
version, model, and active-count (see § “Per-adapter RSS benchmarks”), so a tight cap is
the wrong knob: keep the operator cap as a floor (≥20 for local large workflow-class
workloads) and treat `--cap N` with `N < 20` as a documented incident-time exception.

## Host Coordination

Per-pool `max_concurrency` is not enough when an operator starts several local Metaproc
runs. Multiple small pools can overcommit the same laptop even if each pool’s individual
cap looks conservative.

Current local RunPools use disk-backed host admission before launching subprocesses.
The default namespace is shared by local agent profiles:

```text
~/.metaproc/runpool/host-slots/local-agents/slot-N/
```

Each slot is acquired by atomic directory creation and contains a lease with the parent
RunPool PID, launched child PID, label, pool id, limit, and timestamps.
Stale slots are reclaimed only after the recorded parent and child are gone.

This avoids a daemon and survives sleep or process death.
Process-tree inspection is supporting evidence only; the slot directory is the
coordination primitive.

Execution profiles can set `resources.host_max_concurrency` to cap aggregate local agent
launches. That cap is a safety ceiling, not a statement that the host should always run
that low. Raising it should be based on observed RSS, memory level, swap growth, and
wall-clock needs.

## Locking Primitive

RunPool host coordination uses atomic directory creation as its locking primitive.
A held slot is a directory, and lease metadata inside that directory describes the
owner, child process, timestamps, and stale-reclaim evidence.

Use the existing mkdir-based helpers and patterns for RunPool and RunPool-adjacent
coordination. Do not introduce `flock(2)`, `fcntl.flock`, `fcntl.lockf`, `filelock`,
`portalocker`, or other kernel-state file-locking libraries in this subsystem.

This rule exists because the coordination surface must work on local disks and shared
filesystems such as NFS, Filestore, and SMB:

- `mkdir` is the visible state transition.
  Either the directory exists or it does not, and the lease metadata remains inspectable
  after process death.
- Kernel-held advisory locks can disappear after client or process failure in ways the
  application cannot inspect, which makes stale-state recovery harder to reason about.
- `flock` and `fcntl` semantics differ across platforms and filesystems.
  They are a common agent suggestion, but they are not the RunPool locking primitive.

The reusable helper is `metaproc.io.mkdir_lock`. `host_admission.py` owns the RunPool
slot lease format because it needs lease-content-aware stale checks using PID and
process create-time evidence.

## Visibility Contract

Every substantial local run should make these views useful without raw log tailing:

```bash
uv run metaproc status <run-dir>
uv run metaproc stats <run-dir> --json
uv run metaproc pool status <run-dir>
uv run metaproc pool events <run-dir> --summary
uv run metaproc pool events <run-dir> --type pressure_check --summary
uv run metaproc pool health <run-dir> --summary
uv run metaproc pool host-slots
uv run metaproc pool concurrency-timeline <run-dir>
uv run metaproc trace --extract <run-dir>
uv run metaproc trace --health <run-dir>
```

`health.jsonl` is the primary incident log for resource questions.
It should include:

- aggregate level plus component levels
- available memory percentage
- total RAM when available
- absolute swap and swap growth
- disk free, disk level, and disk pressure cause
- current capacity, effective target, and bottleneck
- active count, active RSS, active peak RSS, and active log bytes

`pool host-slots` is the supported live view for the disk-backed host admission gate.
Operators should use it instead of ad hoc reads of
`~/.metaproc/runpool/host-slots/.../lease.json`.

Readers must handle `.jsonl` and `.jsonl.gz` transparently.
Historical status files from before a new telemetry field existed should remain readable
for operator commands; new writers should still fail loudly if required live telemetry
cannot be collected.

## Operational Rules

- Use one planned dispatch when possible; avoid many unrelated shell launches for the
  same host unless host admission is known to cover them.
- Treat `max_concurrency` as a ceiling.
  Too low wastes the host and can make daily runs miss wall-clock deadlines.
- Prefer a high-enough ceiling plus adaptive control over a permanently low cap.
  For local agent-pool dispatches, hold the operator cap at ≥20 and let the adaptive
  memory and provider ceilings govern under pressure; see § “Adaptive Concurrency” →
  “Operator cap floor”.
- Use `pool override --cap` only for explicit temporary interventions, and clear it once
  host health is stable.
  A `--cap` value below the operator-cap floor (20 for local large workflow-class
  workloads) needs a logbook entry plus a follow-up `--clear`.
- Treat sustained `elevated` memory as a hold state, not an automatic collapse to one
  worker.
- Treat `high` or `critical` memory, or fast positive swap growth, as a reason to reduce
  quickly.
- Treat high absolute swap as context, not proof of current pressure.
- Treat low disk as a separate incident.
  Determine whether the cause is swap growth, active logs, or unrelated disk use before
  changing memory policy.
- Use `metaproc kill` or pool drain/override commands, not ad hoc PID killing.
- Resume interrupted runs with the same `RUN_ID` so completed artifacts are reused and
  orphaned running markers can be reconciled.

## Module Map

| Module | Responsibility |
| --- | --- |
| `pool.py` | Pool manager, adaptive controller, health sampling, status writes |
| `backend.py` | Launch backend protocol and local subprocess backend |
| `host_admission.py` | Disk-backed aggregate host admission across local RunPools |
| `concurrency.py` | Structured concurrency-plan provenance (`ConcurrencyPlan` model) |
| `events.py` | Append-only JSONL event writer for pool events |
| `event_models.py` | Typed pool event schemas |
| `event_reader.py` | Shared JSONL reader for pool event files |
| `process_events.py` | Append-only JSONL event writer for DAG lifecycle events |
| `process_event_models.py` | Typed process/step/item event schemas |
| `status.py` | Pydantic status models and atomic status I/O |
| `kill.py` | External kill/drain protocol |
| `monitor.py` | Process-health helpers |
| `semaphore.py` | Adaptive concurrency primitive |
| `mock_backend.py` | Deterministic backend for tests |
| `registry.py` | Backend registration and resolution |

`metaproc.osutils.memory_pressure` is currently outside `metaproc.runpool` because other
commands can use it.
If RunPool is extracted into a standalone package, the telemetry module should move with
it.

## Testing Rules

RunPool tests should be deterministic by default:

- use `tmp_path` for status and log files
- use `MockBackend` or short local subprocesses
- avoid historical `runs/local` artifacts in CI
- validate historical-artifact compatibility with small synthetic fixtures
- mock macOS `sysctl`, Linux `/proc/meminfo`, and Linux PSI inputs
- gate live historical smoke tests behind explicit environment variables

End-to-end analysis-arb tests should include a RunPool status check that verifies
component levels, swap growth, and active count are visible before and after a fan-out.

## Per-Adapter RSS Benchmarks (macOS, 2026-05-23)

Measurements taken during the 2026-05-25 mon-thru-wed-ensemble batch (see
[the production incident analysis](../arch/arch-runpool.md)) on macOS 25.2.0, Apple
Silicon, 32 GB RAM. Methodology: read `active_rss_bytes` from
`runpool/steps/*/health.jsonl` and divide by `active_count` to get per-process-tree RSS
at 10s sample intervals.
This includes all `psutil.children(recursive=True)` per
[backend.py](../../src/metaproc/runpool/backend.py) lines 350-358.

| Adapter | Old `estimated_process_rss_mb` | Observed P50 | Observed P95 | Recommended | Sample size |
| --- | ---: | ---: | ---: | ---: | --- |
| `claude-opus` | 1536 | 384 MB | 748 MB | **500** | 5,714 samples, 1-8 concurrent, 3 days |
| `claude-sonnet` | 1536 | (same binary as `claude-opus`) | (same) | **500** | — |
| `pi-glm5` | 768 | 176 MB | 989 MB | **500** | 3,324 samples, 1-7 concurrent, 3 days |
| `codex-gpt55` | 4096 | 33 MB (idle) | 53 MB (idle) | **no change** | idle snapshots only; needs active-run telemetry |

**Concurrency impact**: at `kern.memorystatus_level=44` (typical desktop), 32 GB RAM,
`budget_fraction=0.25`, the formula
`initial = (32 × 0.44 × 0.25 × 1024) // estimated_process_rss_mb` gives:

| Adapter | Old initial concurrency | With 500 MB estimate |
| --- | --- | --- |
| `claude-opus` | 2 | 7 |
| `claude-sonnet` | 2 | 7 |
| `pi-glm5` | 4 | 7 |

**Caveats**:

- The naive 1.5× P95 = 1122 MB for `claude-opus` is inflated by single-process samples
  where `active_count=1`. At `active≥3`, P95 drops to 530-641 MB. 500 MB sits at ~1.3×
  P95 in the multi-process regime, which is where concurrency decisions matter.
- The `active=1` P95 of 1303 MB for `pi-glm5` is a single-process spike, likely heavy
  I/O buffering. At `active≥4`, P95 drops to 629-635 MB. The adaptive controller still
  ratchets down under genuine pressure; the estimator need not prevent every possible
  overcommit.
- The idle `ps -o rss` snapshot of the bare claude binary shows ~87 MB. That is the
  **root process only** and excludes the child-process tree that runs Bash, fetch, MCP
  servers, and the agent’s tool subprocesses.
  Use the health jsonl per-tree value (380+ MB) for the estimator.
- **macOS-only measurement.** Linux measurement requires a separate sampling pass on a
  representative host.
  Per the macOS section above, `kern.memorystatus_level` already accounts for inactive
  (reclaimable) pages, so the estimator does not need to add an inactive-page heuristic
  on top.

This closes the prior Open Design Item “Build a stable benchmark for Codex and Claude
RSS by profile and model” for the Claude and pi-cli adapters on macOS. The `codex-gpt55`
and Linux benchmarks remain open.

## Future Considerations

### Open Questions

- Should disk level continue influencing the aggregate capacity level, or become purely
  diagnostic except at near-full disk?
- How should the auth-pool-aware classifier interaction be resolved?
  When N parallel orchestrators share a 2-label OAuth pool, the resulting 429 failures
  get misclassified by the `claude-startup-exit-1-silent` known-bug regex due to
  debug-log prepend ordering in
  `metaproc.dispatch.pool_dispatch.classify_failure_for_slot`. See
  [arch-claude-code-harness.md § False-positive classifier pitfall](arch-claude-code-harness.md)
  for the diagnostic checklist and fix candidates.
  Cost: 16 items permanent-failed on 2026-05-23 batch; ABORT severity prevented retry.

### Potential Improvements

- Add cgroup-aware Linux telemetry for containerized workers.
- Add a pressure-shedding policy for sustained critical memory pressure.
- Build a stable RSS benchmark for `codex-gpt55` and for Linux hosts (Claude and pi-cli
  on macOS are now sampled; see § Per-adapter RSS benchmarks).
- Revisit `codex-gpt55` host cap using observed RSS and swap-growth data from clean
  runs.
- Add Windows support only after a clear telemetry and process-tree design exists.

## References

- [Apple Activity Monitor User Guide: memory usage](https://support.apple.com/guide/activity-monitor/view-memory-usage-actmntr1004/mac)
- [Apple Dispatch memory-pressure source](https://developer.apple.com/documentation/dispatch/dispatch_source_type_memorypressure)
- [Linux proc_meminfo(5)](https://www.man7.org/linux/man-pages/man5/proc_meminfo.5.html)
- [Linux kernel PSI documentation](https://www.kernel.org/doc/html/v6.10/accounting/psi.html)

<!-- This document follows std-doc-guidelines.md.
Review guidelines before editing.
-->
