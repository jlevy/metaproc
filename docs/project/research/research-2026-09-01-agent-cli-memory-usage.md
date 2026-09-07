---
title: Agent CLI Startup Memory
description: Comparative process-tree startup profiles for Gemini CLI, Claude Code, Codex CLI, and Pi, with requirements for reproducible workflow capacity estimates.
date: 2026-09-01
status: Partial
---
# Research: Agent CLI Startup Memory

**Date:** 2026-09-01

**Status:** Matched one-shot controls complete; repeated cold and warm starts,
history-size sweeps, Linux profiles, and workload-matched comparisons remain open

## Overview

An agent CLI does not have one stable memory cost.
Client version, platform, model, configuration, local state, prompt, and tools can
change the complete process tree by several gigabytes before it settles.
A workflow therefore needs a startup curve and state regime, not one RSS constant.

The first matched controls measured these successful, tool-free peaks on one 34 GB ARM64
macOS host:

| Client and state | Peak physical footprint | Peak RSS | Process count | Observed duration |
| --- | ---: | ---: | ---: | ---: |
| Gemini 0.40.1, clean or retention disabled | 0.25-0.26 GB | 0.33-0.35 GB | 4 | 2.2-2.5 s |
| Gemini 0.40.1, copied 3.4 GiB project state | **5.15 GB** | **4.89 GB** | 4 | 19.6 s |
| Gemini 0.55.1, clean or retention disabled | 0.39-0.40 GB | 0.47-0.48 GB | 4 | 3.1-5.3 s |
| Gemini 0.55.1, copied 3.4 GiB project state | **5.07 GB** | **4.68 GB** | 3 | 21.7 s |
| Claude Code 2.1.233 | 0.59 GB | 0.91 GB | 11 | 4.7 s |
| Codex CLI 0.135.0, `gpt-5.5` | 0.86 GB | 0.96 GB | 14 | 7.1 s |
| Pi 0.62.0 | 0.21 GB | 0.21 GB | 2 | 2.3 s |

The Gemini split has a controlled cause: startup session-retention cleanup concurrently
read and parsed every saved conversation in the current project’s accumulated history.
The complete reproduction, storage semantics, source path, and isolation contract live
in
[Gemini CLI Project-State Startup Memory](research-2026-09-01-gemini-cli-project-state-memory.md).

Claude had about 1.5 GB of project history and Codex had about 6.4 GB of active plus 10
GB of archived sessions, yet neither loaded its complete history during the short probe.
Pi had only 12 session files.
History bytes alone are therefore not a capacity variable; the client’s startup access
pattern is.

These single runs establish mechanism and scale.
They are not production capacity distributions.
[Host Memory Accounting and Control](research-2026-09-01-host-memory-accounting-and-control.md)
defines the platform metrics, admission, pacing, and emergency-containment model needed
to turn profiles into safe workflow controls.

## Questions

1. What does one complete agent process tree cost during startup and after it settles?
2. Which state, configuration, model, and workload variables change that curve?
3. Which client-specific mitigations prevent allocation rather than only changing the
   failure mode?
4. How do supported clients compare under repeated matched probes?
5. Which measurements are causal controls, and which remain provisional calibration?
6. What must a workflow record before a profile can become an admission default?

## Method

The measurements ran on a 34 GB ARM64 macOS host.
A passive sampler observed each complete process tree every 0.25 seconds and recorded:

- macOS physical footprint and RSS;
- process count and tree membership;
- host reclaimable memory, compressor growth, swap changes, and pressure level;
- elapsed time, target exit, and agent output;
- whether the observer paused, signalled, or killed the target.

No reported comparison arm was paused, signalled, or killed.
The prompt requested exactly `OK` and prohibited tool calls.
Every row completed successfully without tool use.

| Client | Measured version | Probe mode |
| --- | --- | --- |
| Gemini CLI | 0.40.1 and 0.55.1 | Vertex AI, `gemini-3.6-flash`, noninteractive stream output |
| Claude Code | 2.1.233 | Haiku, print mode, no tools or session persistence |
| Codex CLI | 0.135.0 | `gpt-5.5`, ephemeral exec, read-only sandbox |
| Pi | 0.62.0 | Configured Vertex model, print mode, no tools or session |

These are historical measurement identities, not Metaproc’s current minimum-version
contracts. A version upgrade creates a new profile until matched evidence shows that an
older calibration remains valid.

## Findings

### Process Trees Are the Unit of Measurement

Every client used more than one process in at least one mode.
Root-PID RSS can omit the process that holds the memory, while summed RSS can overcount
shared pages and omit compressed anonymous memory on macOS. The observer must follow the
complete tree and apply the platform metric defined in the host-accounting research.

A comparison also needs the same outcome contract for every arm.
The transcript result, declared output, process exit, and any supervisor or guard action
must be reconciled before calling a run successful or failed.
Exit status alone cannot distinguish client failure from resource preemption or a
wrapper that exits nonzero after the agent has completed.

### Gemini Shows Why State Is Part of Profile Identity

The clean and accumulated-state Gemini arms used the same prompt, model, flags,
repository, and host.
Only the project-state regime changed, and the process-tree peak changed by nearly 5 GB.
Disabling automatic session retention against the same copied bucket removed the spike
on both measured versions.

Changing the working directory had appeared to be a repository-size effect.
It actually selected a different project-state bucket.
The profile must therefore record both the effective local-state contract and any
working-directory rule that selects it.

The detailed Gemini record also shows why a heap cap is not a smaller profile.
A real 2 GB V8 old-space cap produced an OOM after the process tree had already reached
3.28 GB. That arm is a failure mode, not an admissible low-memory regime.

### Stored History Does Not Imply Startup Demand

Claude and Codex had more stored session data than Pi, but their startup peaks were
bounded by what the clients actually opened during the probe.
Gemini’s accumulated-state peak came from a specific unbounded startup access path, not
from Node.js or JSONL storage by itself.

A comparative profile should therefore record at least:

- total relevant local-state bytes and object count;
- which state paths startup reads and why;
- whether the run is cold or warm;
- session-persistence, resume, and retention configuration;
- working directory and client home;
- model, provider, prompt shape, tool policy, and output mode.

### Production Workloads Have Longer Curves

A downstream fan-out workload measured Gemini startup peaks of 3.6-4.6 GB and later
observed 5.3 GB. Across 16 process trees, median time to peak was 33 seconds, the window
above 3 GB averaged 31 seconds and reached 71 seconds, and settled RSS ranged from 76 MB
to 1.2 GB.

The short control isolated a startup mechanism, while the production-shaped run also
loaded context, called tools, and performed model work.
The curves need not match point for point to establish that simultaneous starts are the
dangerous phase. Eight overlapping 4 GB startups require about 32 GB before accounting
for the host, orchestrator, and already-settled agents.
Two same-level Gemini leaves were observed starting in the same second inside one parent
work item, creating about 9 GB of simultaneous demand at the measured peak.

Spacing a parent job is insufficient when that parent can launch several same-level
agent leaves. Admission and pacing must operate at the executable-agent boundary.

The observed curves also invalidated two downstream scalar assumptions: 1.15 GiB per
parent work item and 500 MB per agent process.
Neither represented the startup demand that admission needed to reserve.

### One Peak Is Not a Capacity Distribution

The current rows are useful causal and scale controls, but each non-Gemini client has
only one accepted run.
They do not quantify run-to-run variance, cold versus warm behavior, representative tool
use, or Linux accounting.

The Codex default-model arm was excluded because that installed client could not use the
configured `gpt-5.6-sol` model and logged a model-catalog parse error for the newer
`max` reasoning-effort value.
The explicit `gpt-5.5` arm succeeded.
That result remains a valid historical control but must not become a current default
without a version-aligned rerun.

## Profile Contract for Workflows

A reusable agent memory profile needs more than a scalar:

| Field group | Required identity or evidence |
| --- | --- |
| Runtime | Client and version, platform and architecture, adapter version, provider, model |
| State | Client home, working directory, relevant path sizes and counts, resume and retention configuration |
| Workload | Prompt class, input bytes, tool policy, expected tool use, output mode |
| Startup | Peak cost, time to peak, duration above reservation threshold, compatible launch spacing |
| Steady state | Settled distribution and workload-dependent growth |
| Measurement | Tree or cgroup scope, native metric, cadence, observer overhead, cold or warm arm |
| Outcome | Transcript result, declared outputs, process exit, supervisor result, guard action |
| Provenance | Sample count, raw-series identity, collection date, and invalidation conditions |

The low Gemini regime is valid only after the configured state mitigation is verified.
When the launcher cannot prove that regime, admission must use a conservative high-spike
fallback rather than infer safety from a setting name.

Profiles are inputs to admission and launch pacing.
They do not replace current host evidence, and emergency containment must not use them
as permission to kill work preemptively.

## Workflow Implications

- Maintain startup peak, startup duration, steady cost, and launch-spacing evidence per
  supported profile.
- Include state regime and client version in profile identity.
- Apply client-specific demand reduction before raising host limits.
- Admit every executable agent leaf against current headroom and outstanding startup
  reservations.
- Pace compatible starts across pools and parent jobs, not only inside one scheduler.
- Keep passive profiling distinct from dry-run intervention simulation.
- Preserve complete transcripts and supervisor journals so resource preemption is not
  misdiagnosed as a prompt or model failure.
- Revert to a conservative fallback whenever source behavior, configuration, or
  measurements no longer match the profile.

## Open Evidence Gaps

- Repeat every client across cold and warm starts.
- Run enough samples to report distributions rather than individual peaks.
- Sweep state bytes and object counts independently where the client reads local state.
- Run one common tool-using workload and record complete startup and steady curves.
- Reprofile the CLI versions in Metaproc’s current compatibility contracts.
- Establish equivalent Linux profiles with PSS or cgroup accounting.
- Measure observer overhead and peak-miss risk at candidate production cadences.
- Define profile invalidation and rollout checks for client upgrades.

## Evidence Provenance

The controlled profiles retained a process-tree JSONL series, stdout, stderr, status,
and target identity per arm.
Matched Gemini arms also retained the prompt and native settings.
The passive summaries recorded zero interventions.

The raw profiles and production incident records remain with the downstream workflow
that collected them.
This record preserves the reusable comparative measurements, method, profile contract,
and workflow conclusions; the Gemini-specific causal evidence is preserved in its
companion record.

One historical production sampler used selected command shapes, RSS, and a 15-second
cadence, so it could omit clients and short peaks.
Its old swap field also parsed the `free` label from `vm.swapusage` rather than swap in
use; that column is invalid.
The controlled profiles used the corrected parser and complete-tree observer.

## References

- [Gemini CLI Project-State Startup Memory](research-2026-09-01-gemini-cli-project-state-memory.md)
- [Host Memory Accounting and Control](research-2026-09-01-host-memory-accounting-and-control.md)
- [RunPool Host Safety Envelope](../specs/active/plan-2026-09-01-runpool-host-safety.md)
- [Safeproc Local Incubation](../specs/active/plan-2026-09-01-safeproc-local-incubation.md)
- [Metaproc Gemini adapter](../../../src/metaproc/adapters/gemini_cli.py)

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
