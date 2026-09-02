---
title: Agent CLI Startup Memory
description: Measured process-tree memory profiles for Gemini CLI, Claude Code, Codex CLI, and Pi, including the isolated cause of a Gemini startup spike.
date: 2026-09-01
status: Complete
---
# Research: Agent CLI Startup Memory

**Date:** 2026-09-01

**Status:** Gemini cause reproduced on versions 0.40.1 and 0.55.1 and confirmed in
0.58.0 source; repeated and workload-matched comparisons remain open

## Overview

An agent CLI does not have one stable memory cost.
Startup state, client version, configuration, prompt, tools, and model can change the
complete process tree by several gigabytes before it settles.

The most important measured case is Gemini CLI. The same short prompt, model, flags,
repository, and host peaked near 0.25 GB with clean project state and 5.15 GB with an
accumulated 3.4 GiB project-history bucket on version 0.40.1. Version 0.55.1 reproduced
the split at 0.39 GB and 5.07 GB. Disabling Gemini’s built-in session-retention cleanup
against the same copied state returned the peaks to 0.26 GB and 0.40 GB respectively.
Changing the working directory had appeared to fix the spike because it selected a
different project-state bucket, not because Gemini was scanning fewer repository files.

The accumulated bucket was durable session state, not an API cache.
It contained about 12,300 JSONL conversation files, 2.9 GiB of chat history, 517 MiB of
offloaded tool output, and a negligible project-memory file.
Gemini uses this state for resume, search, rewind, checkpoints, tool-output recovery,
and optional memory mining.
A fresh headless run may isolate it only when its process contract supplies the
settings, authentication, instructions, and artifacts it still needs.

This result changes the safety model in two ways:

- adapter-local state and configuration belong in the identity of a memory profile;
- removing a known allocation source is preferable to raising limits, but host-wide
  admission and launch pacing must still protect against unknown or changed behavior.

The companion
[Host Memory Accounting and Control](research-2026-09-01-host-memory-accounting-and-control.md)
defines the platform gauges and control model.
The [RunPool host-safety plan](../specs/active/plan-2026-09-01-runpool-host-safety.md)
owns the system design, and the
[Safeproc local-incubation plan](../specs/active/plan-2026-09-01-safeproc-local-incubation.md)
owns the standalone package boundary.

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
| Gemini CLI | 0.40.1 and 0.55.1 | Vertex AI, Flash model, noninteractive stream output |
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

The upgraded release preserved the causal result:

| Gemini 0.55.1 arm | Peak footprint | Peak RSS | Observed duration | Outcome |
| --- | ---: | ---: | ---: | --- |
| Clean isolated home | 0.39 GB | 0.47 GB | 3.1 s | success |
| Clean home plus copied 3.4 GiB project history | **5.07 GB** | **4.68 GB** | 21.7 s | success |
| Same copied history, session retention disabled | **0.40 GB** | **0.48 GB** | 5.3 s | success |

The two state-bearing 0.55.1 arms used independent filesystem clones so the first
cleanup could not alter the second arm’s input.

### Cross-Version Source Confirmation

The official 0.40.1 source matches the experiment.
A 2026-09-01 review of 0.55.1, 0.58.0, and upstream commit
`4963a4456a886bb6af7dcfb807ad6e3e46ce46fc` found the same memory-critical path:

1. Session cleanup is enabled by default through
   [`general.sessionRetention.enabled`](https://github.com/google-gemini/gemini-cli/blob/v0.58.0/docs/cli/settings.md).
2. Startup launches
   [`cleanupExpiredSessions`](https://github.com/google-gemini/gemini-cli/blob/v0.58.0/packages/cli/src/utils/sessionCleanup.ts)
   without awaiting it, so cleanup overlaps authentication and model work.
3. [`getAllSessionFiles`](https://github.com/google-gemini/gemini-cli/blob/v0.58.0/packages/cli/src/utils/sessionUtils.ts)
   creates one asynchronous load per session and waits on an unbounded `Promise.all`.
4. [`loadConversationRecord`](https://github.com/google-gemini/gemini-cli/blob/v0.58.0/packages/core/src/services/chatRecordingService.ts)
   opens each file, decodes and parses every JSONL line, and reconstructs session
   metadata even in metadata-only mode.

The relevant files were unchanged between 0.55.1 and 0.58.0, and the reviewed upstream
commit retained the unbounded enumeration and full-line parsing.
Newer releases had improved corrupt-session handling and related cleanup, but had not
introduced a metadata index or bounded concurrency at the reviewed commit.

The behavior is version-specific.
A supported Gemini upgrade must recheck both source and measurements before retaining
this explanation or its calibration.

### Project State and Headless Isolation

Current Gemini versions map a normalized project root to a project-scoped directory
under the Gemini home.
Changing the working directory can therefore select a fresh state bucket, but the
directory itself is not the causal memory input.

`GEMINI_CLI_HOME` is broader than a session-directory override.
Gemini also resolves settings, credentials, instructions, commands, skills, policies,
the project registry, and project state relative to that home.
A launcher that selects an isolated home must re-supply every required part of that
contract rather than assuming host authentication and policy remain visible.

For a fresh invocation that does not resume, search, rewind, or recover a prior session,
the old chat transcripts and their matching offloaded tool-output files need not be
shared with the new run.
Required repository inputs, explicit prompts, authentication, settings, policies, and
intended project memory remain independent inputs and must be preserved deliberately.
Disabling automatic retention prevents the startup cleanup scan; it does not delete
accumulated history or provide an external retention policy.

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
| Gemini 0.40.1, clean or retention disabled | 0.25-0.26 GB | 0.33-0.35 GB | 4 | 2.2-2.5 s |
| Gemini 0.40.1, copied 3.4 GiB project history | **5.15 GB** | **4.89 GB** | 4 | 19.6 s |
| Gemini 0.55.1, clean or retention disabled | 0.39-0.40 GB | 0.47-0.48 GB | 4 | 3.1-5.3 s |
| Gemini 0.55.1, copied 3.4 GiB project history | **5.07 GB** | **4.68 GB** | 3 | 21.7 s |
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
- Headless Gemini runs on the measured versions should disable automatic session cleanup
  or use isolated, bounded state.
  Disabling cleanup does not delete old history, so retention remains a separate
  maintenance operation.
- Per-run working directories remain useful isolation, but documentation must not call
  the result repository-scan avoidance.
- At this repository commit, the Gemini adapter accepts `no_session_persistence` as a
  configuration key but does not consume it in command or environment construction.
  The implementation plan must either give that setting a tested Gemini-native contract
  or reject it rather than treating its presence as mitigation.
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
- Revalidate the retention implementation, native setting, and isolated-home contract on
  each supported Gemini release.
- Align the Codex client and model catalog before treating its one-shot result as a
  formal default.
- Establish equivalent Linux profiles with PSS or cgroup accounting.

## References

- [Gemini CLI 0.40.1](https://github.com/google-gemini/gemini-cli/tree/v0.40.1)
- [Gemini CLI 0.58.0](https://github.com/google-gemini/gemini-cli/tree/v0.58.0)
- [Gemini CLI settings](https://github.com/google-gemini/gemini-cli/blob/v0.58.0/docs/cli/settings.md)
- [Gemini CLI session management](https://github.com/google-gemini/gemini-cli/blob/v0.58.0/docs/cli/session-management.md)
- [Gemini CLI shared-environment isolation](https://github.com/google-gemini/gemini-cli/blob/v0.58.0/docs/cli/enterprise.md#user-isolation-in-shared-environments)
- [Metaproc Gemini adapter](../../../src/metaproc/adapters/gemini.py)
- [Host Memory Accounting and Control](research-2026-09-01-host-memory-accounting-and-control.md)
- [RunPool host-safety plan](../specs/active/plan-2026-09-01-runpool-host-safety.md)
- [Safeproc local-incubation plan](../specs/active/plan-2026-09-01-safeproc-local-incubation.md)

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
