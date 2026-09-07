---
title: Gemini CLI Project-State Startup Memory
description: Controlled reproduction and source analysis of Gemini CLI's project-history startup allocation, with state-isolation and orchestration guidance.
date: 2026-09-01
status: Complete
---
# Research: Gemini CLI Project-State Startup Memory

**Date:** 2026-09-01

**Status:** Cause reproduced on versions 0.40.1 and 0.55.1 and confirmed in 0.58.0
source; later versions require revalidation

## Overview

Gemini CLI can be either a roughly 0.4 GB or a 5 GB process tree for the same short
prompt, model, flags, repository, and host.
The difference is accumulated project-scoped session state.
At startup, the measured Gemini versions launch session-retention cleanup, which
concurrently reads and parses every saved conversation for the current project before it
knows which sessions should be removed.

The causal controls were:

- a clean Gemini home stayed at 0.25 GB from an empty directory, a small worktree, and a
  24 GB checkout;
- copying only one accumulated 3.4 GiB project-state bucket into that clean home raised
  the peak to 5.15 GB;
- disabling `general.sessionRetention.enabled` against the same copied bucket reduced
  the peak to 0.26 GB;
- bypassing Gemini’s launcher preserved the spike, while a real 2 GB V8 old-space cap
  converted it into an exit-134 heap OOM after the tree had already reached 3.28 GB.

Version 0.55.1 reproduced the split at 0.39 GB with clean state, 5.07 GB with the copied
bucket, and 0.40 GB with the bucket present but retention disabled.
The memory-critical implementation remained present in 0.58.0 and in the reviewed
upstream commit.

The state directory is not an API-response cache or a model prompt cache.
It is a durable session store used for resume, search, rewind, checkpoints, session
deletion, interruption recovery, tool-output recovery, and optional memory mining.
A fresh headless invocation can omit prior session state only when its process contract
does not require those features and supplies authentication, settings, instructions, and
run artifacts separately.

The companion [Agent CLI Startup Memory](research-2026-09-01-agent-cli-memory-usage.md)
compares the measured demand with Claude Code, Codex CLI, and Pi.
[Host Memory Accounting and Control](research-2026-09-01-host-memory-accounting-and-control.md)
defines the platform gauges and workflow controls that remain necessary after this
client-specific cause is removed.

## Questions

1. Which input causes the multi-gigabyte startup allocation?
2. What does the project-state directory contain, and why does it grow?
3. Which source path reads that state during startup?
4. Why did changing the working directory appear to fix the problem?
5. Which state may a fresh orchestrated invocation omit safely?
6. Which mitigations prevent the allocation, and which only change how it fails?

## Scope and Method

The controlled experiments ran on a 34 GB ARM64 host with macOS 26.5.2. A passive
sampler observed the complete process tree every 0.25 seconds and recorded physical
footprint, RSS, process count, host reclaimable memory, compressor and swap changes,
pressure state, target exit, and output.
It did not pause, signal, or kill the target.

Every reported arm used Vertex AI, `gemini-3.6-flash`, a short prompt requesting exactly
`OK`, and noninteractive stream output.
The 0.40.1 state bucket was a filesystem clone, and the two state-bearing 0.55.1 arms
used independent clones so one cleanup pass could not change the other arm’s input.
The copied bucket excluded user settings, extensions, hooks, credentials, and repository
files.

The reviewed upstream refs were:

| Ref | Commit | Use |
| --- | --- | --- |
| `v0.40.1` | `7a382e066ffe36d6ee94b3abbd0c9d22c97f5620` | Original installed reproduction |
| `v0.55.1` | `41327e407da58aa01c409ef6685b7b5d379f295e` | Upgraded installed reproduction |
| `v0.58.0` | `ac9431c9e2290d68af31a77614ff2fddb2391ca3` | Stable source review |
| reviewed `main` | `4963a4456a886bb6af7dcfb807ad6e3e46ce46fc` | Post-release source check |

## Isolation Results

### Gemini CLI 0.40.1

| Arm | Peak footprint | Peak RSS | Observed duration | Outcome |
| --- | ---: | ---: | ---: | --- |
| Clean home, isolated directory | 0.25 GB | 0.34 GB | 2.2 s | success |
| Clean home, small worktree | 0.25 GB | 0.33 GB | 2.2 s | success |
| Clean home, 24 GB checkout | 0.25 GB | 0.33 GB | 2.4 s | success |
| Clean home, large checkout, workload-like flags | 0.25 GB | 0.33 GB | 2.5 s | success |
| Ordinary user state | 5.00 GB | 4.60 GB | 20.8 s | success |
| Clean home plus hooks only | 0.26 GB | 0.35 GB | 3.4 s | success |
| Clean home plus copied 3.4 GiB project state | **5.15 GB** | **4.89 GB** | 19.6 s | success |
| Same copied state, session retention disabled | **0.26 GB** | **0.35 GB** | 2.2 s | success |
| Same copied state, launcher bypassed | 4.87 GB | 4.40 GB | 20.0 s | success |
| Same copied state, launcher bypassed, 2 GB old-space cap | 3.28 GB | 3.23 GB | 3.4 s | exit 134, V8 OOM |

The clean-home, large-checkout arm rules out repository size and content as the cause.
The copied-state and retention-disabled pair holds the large state constant and isolates
automatic session cleanup.
The hooks-only arm separately rules out the configured startup hook.

### Gemini CLI 0.55.1

| Arm | Peak footprint | Peak RSS | Observed duration | Outcome |
| --- | ---: | ---: | ---: | --- |
| Clean isolated home | 0.39 GB | 0.47 GB | 3.1 s | success |
| Clean home plus copied 3.4 GiB project state | **5.07 GB** | **4.68 GB** | 21.7 s | success |
| Same copied state, session retention disabled | **0.40 GB** | **0.48 GB** | 5.3 s | success |

Both state-bearing arms used independent clones.
The reproduction therefore survived the client upgrade and did not depend on a cleanup
performed by the first arm.

## What the Project Bucket Stores

The measured project directory held:

| Path under `~/.gemini/tmp/<project>` | Size or count | Purpose |
| --- | ---: | --- |
| `chats/` | 2.9 GiB; 12,313 JSONL files | Automatically saved prompts, responses, thoughts, token counts, tool calls, outputs, rewind records, and session metadata |
| `tool-outputs/` | 517 MiB; 4,247 files | Large tool strings moved out of active model context and replaced there with a marker and file path |
| `memory/` | 4 KiB; one file | Private project memory loaded into future prompts and used by experimental Auto Memory |
| complete bucket | 3.4 GiB | The above plus small project-local artifacts and ownership metadata |

The term *cache* is misleading for this directory.
The data is durable product state with user-visible recovery and navigation behavior.
Removing it may be correct for a new stateless invocation, but it is not equivalent to
discarding recomputable API responses.

The complete Gemini `~/.gemini/tmp` tree occupied about 5.0 GB; the measured project
bucket accounted for about 3.4 GiB of it.
The measured growth followed from four behaviors:

- substantive headless and interactive sessions are recorded automatically;
- the default retention window is 30 days and the default `maxCount` is unlimited;
- a high-rate orchestrator can create thousands of sessions before any becomes old
  enough for age retention;
- the JSONL recording is an append-only mutation log, so updated message and tool-call
  state is appended under the same logical ID rather than compacted in place.

The observed 12,313 transcripts covered 2026-08-04 through 2026-09-01, less than one
month. Offloaded tool strings remain useful to a resumable session without occupying its
active model context, but their files remain until the associated session is deleted.

## Source Path

The official source explains the allocation shape:

1. Startup initializes project storage, then launches
   [`cleanupExpiredSessions()`](https://github.com/google-gemini/gemini-cli/blob/v0.58.0/packages/cli/src/gemini.tsx#L635-L672)
   without awaiting it.
   Cleanup overlaps authentication, prompt setup, and model work rather than running at
   a quiet phase boundary.
2. Cleanup is enabled by default and calls
   [`getAllSessionFiles()`](https://github.com/google-gemini/gemini-cli/blob/v0.58.0/packages/cli/src/utils/sessionCleanup.ts#L102-L145)
   for the current project’s `chats` directory before it knows which sessions are old.
3. [`getAllSessionFiles()`](https://github.com/google-gemini/gemini-cli/blob/v0.58.0/packages/cli/src/utils/sessionUtils.ts#L237-L345)
   creates one asynchronous load per session and waits on an unbounded `Promise.all`.
4. [`loadConversationRecord(..., metadataOnly: true)`](https://github.com/google-gemini/gemini-cli/blob/v0.58.0/packages/core/src/services/chatRecordingService.ts#L133-L395)
   still opens every file, streams every line, decodes it, parses JSON, and tracks
   message identity and resumability.
   Metadata-only mode reduces retained output; it does not read only a metadata header.

The concurrent buffers, decoded strings, and parsed records account for the anonymous
allocation previously observed as VM Tag 255 and `DefaultMallocZone` growth in the
process footprint. The core one-load-per-file and `Promise.all` design dates to the
original retention implementation.
The relevant startup, cleanup, enumeration, and recording files were unchanged between
0.55.1 and 0.58.0, and the reviewed upstream commit retained the same shape.

Later releases had improved corrupt-session handling, resumability filtering, JSONL
migration, subagent cleanup, and session-linked tool-output cleanup.
They had not introduced bounded enumeration or a metadata index at the reviewed commit.

## Working Directory and the Three Meanings of Memory

Gemini maps the normalized project root to a human-readable slug in
`~/.gemini/projects.json`, then uses `~/.gemini/tmp/<slug>` as the project bucket.
The current
[`Storage`](https://github.com/google-gemini/gemini-cli/blob/v0.58.0/packages/core/src/config/storage.ts#L181-L272)
and
[`ProjectRegistry`](https://github.com/google-gemini/gemini-cli/blob/v0.58.0/packages/core/src/config/projectRegistry.ts#L28-L31)
implement that registry-backed routing and migrate older hash directories.

Changing the working directory appeared to fix memory because it selected a fresh or
smaller bucket. Gemini was not scanning less repository content.
Per-run working directories remain useful isolation, but their mechanism should be
documented accurately.

Three unrelated concepts use the word *memory*:

- the process tree’s physical footprint is host RAM consumption;
- hierarchical `GEMINI.md` and private project `memory/MEMORY.md` content becomes model
  context for a fresh session;
- experimental Auto Memory can mine eligible transcripts for reviewable memory and skill
  candidates.

The private memory file was only 4 KiB. Auto Memory is disabled by default and was
disabled in the measured profiles.
Neither caused the multi-gigabyte allocation; the decisive switch was session retention.

## Heap Profiles and Caps

An earlier `--heap-prof` retained-object profile was only about 51 MB. That did not
measure the transient allocation volume from short-lived buffers, decoded strings,
parsed records, and garbage-collection timing.
A small retained heap therefore did not contradict the process-tree peak.

The ordinary launcher also appended a 16 GB V8 old-space setting to the worker, which
made an earlier `NODE_OPTIONS=--max-old-space-size=2048` control ineffective.
When the launcher was bypassed, the 2 GB limit produced a native stack through V8 string
allocation, Node string decoding, filesystem callbacks, and libuv before aborting with
`FATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory`.

A heap cap is not a safe mitigation.
It changes a transient success into a startup crash after the host has already absorbed
a multi-gigabyte allocation.

## State Boundary for Orchestrated Runs

The native setting that prevents the measured cleanup scan is:

```json
{
  "general": {
    "sessionRetention": {
      "enabled": false
    }
  }
}
```

This setting prevents automatic cleanup.
It does not delete accumulated history and also prevents tool-output cleanup governed by
the same switch. External bounded retention is therefore a separate operation.

| State | Fresh headless invocation | Resume or recovery |
| --- | --- | --- |
| Explicit prompt, repository files, and run artifacts | Preserve; these are intended inputs | Preserve |
| Authentication and provider environment | Preserve or re-supply explicitly | Preserve or re-supply explicitly |
| Required settings, trust, policies, extensions, MCP definitions, commands, and skills | Re-supply the parts the workflow uses | Preserve the parts the resumed workflow used |
| Workspace `.gemini` instructions and settings | Preserve when the process contract relies on them | Preserve |
| `tmp/<project>/chats` | Omit when the run does not use `--resume`, browser, search, rewind, or checkpoint behavior | Preserve the selected session transcript |
| Matching `tool-outputs/session-<id>` | Omit for an unrelated new session | Preserve when a resumed transcript may reread masked output |
| Project `memory/` | Omit only when deterministic orchestration intentionally excludes private and Auto Memory | Preserve when that memory is intended to affect later prompts |
| Session plans, task state, and subagent records | Omit for an unrelated new session | Preserve when the resumed workflow or audit contract needs them |

`GEMINI_CLI_HOME` isolates more than the project bucket.
Gemini resolves settings, credentials, global instructions, commands, skills, policies,
the project registry, and project state below `$GEMINI_CLI_HOME/.gemini`. A production
launcher must not point it at an empty directory and assume authentication and policy
remain visible. The isolated 0.55.1 probes succeeded by using host application-default
credentials, supplying provider location and project explicitly, and writing a minimal
settings file.

At the recorded Metaproc commit, the generic `no_session_persistence` field is accepted
by the Gemini adapter but does not affect command or environment construction.
Gemini CLI 0.58.0 also has no equivalent headless flag that disables recording.
The adapter must either implement a tested native isolation contract or reject the
misleading field.

The cleanest stateless design is a controlled minimal Gemini home or project bucket per
run, with explicit prompt and artifact inputs and external cleanup after required
evidence is published.
When resume is required, retain the named run’s transcript and matching artifacts rather
than sharing every session created by the project.

## Workflow Implications

- Disable automatic session retention or provide isolated, bounded state for fresh
  headless invocations on versions with the measured implementation.
- Treat client version, platform, model, settings, project-state regime, and working
  directory as memory-profile identity.
- Verify the low-memory regime with a large copied-state control before using its
  calibration for admission.
- Preserve a conservative high-spike fallback whenever the state regime cannot be
  proved.
- Keep host-wide admission and launch pacing after removing the known scan; client
  behavior and other startup costs can change.
- Do not raise emergency-guard thresholds or use a V8 heap cap to accommodate a
  preventable allocation.
- Report the unbounded scan upstream with both version reproductions and the stable
  source path.

## Open Evidence Gaps

- Repeat state-size sweeps with session count and bytes varied independently.
- Revalidate source and measurements on every supported Gemini release.
- Test the deployed adapter mitigation against a large copied bucket.
- Measure common tool-using workloads after mitigation rather than extrapolating from a
  short prompt.
- Define and test the adapter’s session-persistence contract.
- Confirm an external retention policy preserves resumable transcripts and their
  matching tool-output files together.

## Evidence Provenance

The controlled profiles retained one JSONL process-tree series, stdout, stderr, status,
and target identity per arm, plus the prompt and native settings for matched Gemini
arms. The passive summaries recorded zero interventions.
Those raw profiles and the production incident records remain with the downstream
workflow that collected them; this document preserves their reusable measurements,
causal controls, source analysis, and orchestration conclusions.

## References

- [Gemini CLI 0.40.1 source](https://github.com/google-gemini/gemini-cli/tree/v0.40.1)
- [Gemini CLI 0.55.1 source](https://github.com/google-gemini/gemini-cli/tree/v0.55.1)
- [Gemini CLI 0.58.0 source](https://github.com/google-gemini/gemini-cli/tree/v0.58.0)
- [Gemini CLI settings](https://github.com/google-gemini/gemini-cli/blob/v0.58.0/docs/cli/settings.md)
- [Gemini CLI session management](https://github.com/google-gemini/gemini-cli/blob/v0.58.0/docs/cli/session-management.md)
- [Gemini CLI shared-environment isolation](https://github.com/google-gemini/gemini-cli/blob/v0.58.0/docs/cli/enterprise.md#user-isolation-in-shared-environments)
- [Metaproc Gemini adapter](../../../src/metaproc/adapters/gemini_cli.py)
- [Agent CLI Startup Memory](research-2026-09-01-agent-cli-memory-usage.md)
- [Host Memory Accounting and Control](research-2026-09-01-host-memory-accounting-and-control.md)
- [RunPool Host Safety Envelope](../specs/active/plan-2026-09-01-runpool-host-safety.md)

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
