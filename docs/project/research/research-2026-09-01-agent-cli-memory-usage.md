---
title: Agent CLI Startup Memory
description: Measured process-tree memory profiles for Gemini CLI, Claude Code, Codex CLI, and Pi, including the isolated cause of a Gemini startup spike.
date: 2026-09-01
status: Complete
---
# Research: Agent CLI Startup Memory

**Date:** 2026-09-01

**Status:** Gemini cause established for version 0.40.1; repeated and workload-matched
comparisons remain open

## Overview

An agent CLI does not have one stable memory cost.
Startup state, client version, configuration, prompt, tools, and model can change the
complete process tree by several gigabytes before it settles.

The most important measured case is Gemini CLI 0.40.1. The same short prompt and model
peaked near 0.25 GB with clean project state and 5.15 GB with an accumulated 3.4 GB
project-history bucket.
Disabling Gemini’s built-in session-retention cleanup against that same copied state
returned the peak to 0.26 GB. Changing the working directory had appeared to fix the
spike because it selected a different project-state bucket, not because Gemini was
scanning fewer repository files.

This result changes the safety model in two ways:

- adapter-local state and configuration belong in the identity of a memory profile;
- removing a known allocation source is preferable to raising limits, but host-wide
  admission and launch pacing must still protect against unknown or changed behavior.

The companion
[Host Memory Accounting and Control](research-2026-09-01-host-memory-accounting-and-control.md)
defines the platform gauges and control model.
The [RunPool host-safety plan](../specs/active/plan-2026-09-01-runpool-host-safety.md)
owns implementation.

## Questions

1. What does one agent process tree cost during startup and after it settles?
2. Which local state and configuration variables change that curve?
3. Which controls prevent a large allocation, and which merely turn it into a crash?
4. How large are matched one-shot startup controls for other supported CLIs?
5. Which conclusions are causal, and which still require repeated measurement?

## Method

Measurements ran on a 34 GB ARM64 macOS host.
A passive sampler observed the complete process tree every 0.25 seconds and recorded
physical footprint, RSS, process count, host reclaimable memory, compressor and swap
changes, pressure state, target exit, and output.
It did not pause or signal the target.

The cross-CLI probe asked for exactly `OK` and prohibited tool calls.
Every comparison row below completed successfully without tool use.
These are startup controls, not representative production workloads.

| Client | Version | Probe mode |
| --- | --- | --- |
| Gemini CLI | 0.40.1 | Vertex AI, Flash model, noninteractive stream output |
| Claude Code | 2.1.233 | Haiku, print mode, no tools or session persistence |
| Codex CLI | 0.135.0 | `gpt-5.5`, ephemeral exec, read-only sandbox |
| Pi | 0.62.0 | Configured Vertex model, print mode, no tools or session |

## Gemini Session-Retention Finding

### Isolation Results

The experiment changed one input at a time:

| Gemini 0.40.1 arm | Peak footprint | Peak RSS | Observed duration | Outcome |
| --- | ---: | ---: | ---: | --- |
| Clean home, isolated directory | 0.25 GB | 0.34 GB | 2.2 s | success |
| Clean home, small worktree | 0.25 GB | 0.33 GB | 2.2 s | success |
| Clean home, 24 GB checkout | 0.25 GB | 0.33 GB | 2.4 s | success |
| Clean home, large checkout, workload-like flags | 0.25 GB | 0.33 GB | 2.5 s | success |
| Ordinary user state | 5.00 GB | 4.60 GB | 20.8 s | success |
| Clean home plus hooks only | 0.26 GB | 0.35 GB | 3.4 s | success |
| Clean home plus copied 3.4 GB project history | **5.15 GB** | **4.89 GB** | 19.6 s | success |
| Same copied history, session retention disabled | **0.26 GB** | **0.35 GB** | 2.2 s | success |
| Same copied history, launcher bypassed | 4.87 GB | 4.40 GB | 20.0 s | success |
| Same copied history, launcher bypassed, 2 GB V8 old-space cap | 3.28 GB | 3.23 GB | 3.4 s | exit 134, V8 OOM |

The copied state contained about 12,300 JSONL conversation files and 3.4 GB of logical
data. It excluded user settings, hooks, extensions, credentials, and repository files.
The clean-home large-checkout arm rules out checkout size as the cause.
The copied-state and retention-disabled pair holds the history constant and isolates
automatic session cleanup as the cause.

### Source Path in Gemini CLI 0.40.1

The official 0.40.1 source matches the experiment:

1. Session cleanup is enabled by default through
   [`general.sessionRetention.enabled`](https://github.com/google-gemini/gemini-cli/blob/v0.40.1/docs/cli/settings.md).
2. Startup calls
   [`cleanupExpiredSessions`](https://github.com/google-gemini/gemini-cli/blob/v0.40.1/packages/cli/src/utils/sessionCleanup.ts),
   which enumerates the current project’s chat directory.
3. [`getAllSessionFiles`](https://github.com/google-gemini/gemini-cli/blob/v0.40.1/packages/cli/src/utils/sessionUtils.ts)
   creates one promise per session and waits on `Promise.all`.
4. [`loadConversationRecord`](https://github.com/google-gemini/gemini-cli/blob/v0.40.1/packages/core/src/services/chatRecordingService.ts)
   still streams and parses every JSONL line in metadata-only mode.

The behavior is version-specific.
A supported Gemini upgrade must recheck both source and measurements before retaining
this explanation or its calibration.

### Heap Caps Do Not Solve the Problem

The default launcher gave the worker a large V8 old-space allowance.
Bypassing that launcher preserved the allocation spike.
Enforcing a real 2 GB old-space cap caused a heap OOM after the process tree had already
reached 3.28 GB.

A heap cap therefore changes this transient from a successful but dangerous startup into
an early crash. It is not admission control and should not be used as the host safety
mechanism.

## Cross-CLI Startup Controls

The first matched one-shot comparison establishes scale and rules out a universal Node
CLI effect:

| Client and state | Peak footprint | Peak RSS | Process count | Observed duration |
| --- | ---: | ---: | ---: | ---: |
| Gemini, clean or retention disabled | 0.25-0.26 GB | 0.33-0.35 GB | 4 | 2.2-2.5 s |
| Gemini, copied 3.4 GB project history | **5.15 GB** | **4.89 GB** | 4 | 19.6 s |
| Claude Code 2.1.233 | 0.59 GB | 0.91 GB | 11 | 4.7 s |
| Codex CLI 0.135.0, `gpt-5.5` | 0.86 GB | 0.96 GB | 14 | 7.1 s |
| Pi 0.62.0 | 0.21 GB | 0.21 GB | 2 | 2.3 s |

Claude and Codex both had gigabytes of stored history, but neither loaded all of it at
startup. Total history size is therefore not the comparison variable; the client’s
startup access pattern is.

One run per client is not a capacity distribution.
Repeated cold and warm starts, history-size sweeps, and a common tool-using workload
remain necessary before setting durable defaults.

## Production-Shaped Observations

A downstream fan-out workload measured Gemini startup peaks of 3.6-4.6 GB and later
observed 5.3 GB. Across 16 process trees, median time to peak was 33 seconds, the window
above 3 GB averaged 31 seconds and reached 71 seconds, and settled RSS ranged from 76 MB
to 1.2 GB.

Eight simultaneous 4 GB startups require roughly 32 GB before accounting for the host,
the orchestrator, or settled workers.
The same steady concurrency may be sustainable when starts are admitted and paced at the
executable-agent boundary.
Spacing only a parent job is insufficient when one parent can launch several same-level
agent leaves.

## Implications

- A profile needs startup peak, startup duration, steady cost, and launch spacing rather
  than one process-memory scalar.
- Profile identity must include client version, platform, model, and the relevant
  adapter-state regime.
  Working directory matters only when it selects different state.
- Headless Gemini 0.40.1 runs should disable automatic session cleanup or use isolated,
  bounded state. Disabling cleanup does not delete old history, so retention remains a
  separate maintenance operation.
- Per-run working directories remain useful isolation, but documentation must not call
  the result repository-scan avoidance.
- Known adapter mitigations reduce demand; they do not replace fail-closed host
  admission, startup reservations, or cross-client pacing.
- Passive profiling must be distinct from a dry-run intervention mode.
  A measurement command may not pause or signal the target.
- Exit status alone is not enough to diagnose an agent failure.
  A higher layer should reconcile the transcript result, declared outputs, supervisor
  result, and any guard action before retrying or changing prompts.

## Open Evidence Gaps

- Repeat every client across cold and warm starts.
- Sweep Gemini session count and bytes independently.
- Run a common tool-using workload and record complete startup curves.
- Revalidate the retention implementation and setting on each supported Gemini release.
- Align the Codex client and model catalog before treating its one-shot result as a
  formal default.
- Establish equivalent Linux profiles with PSS or cgroup accounting.

## References

- [Gemini CLI 0.40.1](https://github.com/google-gemini/gemini-cli/tree/v0.40.1)
- [Gemini CLI settings](https://github.com/google-gemini/gemini-cli/blob/v0.40.1/docs/cli/settings.md)
- [Host Memory Accounting and Control](research-2026-09-01-host-memory-accounting-and-control.md)
- [RunPool host-safety plan](../specs/active/plan-2026-09-01-runpool-host-safety.md)

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
