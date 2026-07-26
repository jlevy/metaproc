---
title: "Architecture: Claude Code Harness"
description: How metaproc drives `claude` as a non-interactive subprocess, the env / settings / permission interactions that govern it, and the design choices that prevent silent credential or permission failures.
author: metaproc team
status: Approved
---
# Architecture: Claude Code Harness

**Date:** 2026-04-30 (last updated 2026-05-23) **Status:** Approved

> **Maintenance**: This is a maintained architecture doc.
> Revise via `tbd shortcut revise-architecture-doc` (which prompts you to verify content
> against current code, then add a “Future Considerations” section).
> When you make non-trivial changes, bump the **last updated** date above.
> The full arch-doc index lives in
> [development.md § Architecture docs](../development.md#architecture-docs).
> 
> Companion docs (in `metaproc/docs/`): [arch-metaproc-core](arch-metaproc-core.md),
> [arch-runpool](arch-runpool.md), [arch-cloud-execution](arch-cloud-execution.md),
> [arch-authentication](arch-authentication.md), [arch-testing](arch-testing.md).

## Overview

metaproc dispatches earnings-prediction work by spawning the Claude Code CLI (`claude`)
as a non-interactive subprocess for each unit of work.
The slot adapter
([src/metaproc/adapters/claude_code.py](../../src/metaproc/adapters/claude_code.py)) is
responsible for materializing per-attempt credentials, scoping the inner process’s
environment so the wrong account can’t be used, and assembling a command line that lets
the agent actually do its job (write files, run shell tools, search the web) without any
interactive prompts.

The CLI’s behavior here is not obvious: several of its defaults assume an interactive
operator at the keyboard, and several of its hardening features deliberately override
flags we’d otherwise pass.
Edits to the slot adapter, the dispatch wrapper, or the credential pool need this
context.

## Goals and Non-Goals

### Goals

- Pin every credential to a per-attempt slot so cross-account leakage is structurally
  impossible.
- Make the inner `claude` process able to use Bash / Write / Edit / Read tools without
  interactive prompts, in a way that survives the CLI’s own subprocess hardening
  features.
- Disable update-mid-run and session-persistence behaviors that would mix unverified
  versions or leak state between runs.
- Surface failures loudly: a slot that can’t acquire its credential, or whose permission
  environment is broken, must terminate the attempt — not silently fall back to ambient
  operator state.

### Non-Goals

- Replacing the `claude` CLI with the Anthropic SDK directly.
  The CLI is the authoritative tool-use runtime; bypassing it would force
  re-implementing its prompt format, tool set, and lifecycle.
- Supporting unattended dispatches in environments where the operator hasn’t pre-pushed
  a credential to the pool.
  The pool is the single source of truth.
- Defending against a malicious operator.
  Hardening targets accidental misuse (env leakage, prompt injection in fetched web
  content) and CLI bugs, not insider threats.

## Background — What the CLI Actually Reads at Startup

The `claude` CLI’s behavior at startup is shaped by **four input surfaces** that
interact in subtle ways.
The harness has to coordinate all four for a non-interactive run to succeed.

### Surface 1: Credentials

The CLI looks up authentication in a precedence chain documented at
[code.claude.com/docs/en/iam.md](https://code.claude.com/docs/en/iam.md):

1. Cloud-provider mode (Vertex AI, Bedrock).
2. `ANTHROPIC_AUTH_TOKEN` (custom OAuth bearer).
3. `ANTHROPIC_API_KEY` (Anthropic API key).
4. `apiKeyHelper` (settings-declared command that prints a key).
5. `CLAUDE_CODE_OAUTH_TOKEN` (static OAuth bearer for setup-tokens).
6. Stored login credentials at `<CLAUDE_CONFIG_DIR>/.credentials.json`.

Higher entries win unconditionally.
The harness’s job is to make sure exactly one entry resolves to the *intended* account.

### Surface 2: `CLAUDE_CONFIG_DIR`

When set, every reference to `~/.claude/` in the user-global scope is redirected under
the value of `CLAUDE_CONFIG_DIR` instead.
Per
[code.claude.com/docs/en/claude-directory.md](https://code.claude.com/docs/en/claude-directory.md):
this scopes credentials, user-global `settings.json`, user-global `CLAUDE.md`, MCP
config, and auto-memory.
**It does not scope project-level `.claude/` directories** — those are always read
relative to cwd. The harness sets `CLAUDE_CONFIG_DIR=<slot_dir>` so the inner CLI’s
credential lookup is isolated to a per-attempt directory.

Known CLI-side bugs in this area (open against
[anthropics/claude-code](https://github.com/anthropics/claude-code) as of 2026-04-30):
#47056 (still loads `~/.claude/CLAUDE.md` when scoped), #42217 (MCP config not loaded
when scoped), #30538 (VS Code extension ignores it), #47661 (no isolation on Linux/WSL).
The harness compensates for these by materializing credentials directly into the slot
dir rather than depending on CLI lookup behavior, and by refusing to run when stray
files in the slot would cross the credential precedence chain.

### Surface 3: Settings hierarchy

Per [code.claude.com/docs/en/settings.md](https://code.claude.com/docs/en/settings.md),
settings are merged from: managed (highest) → CLI args → project
`.claude/settings.local.json` → project `.claude/settings.json` → user-global
`<CLAUDE_CONFIG_DIR>/settings.json` (lowest).
Array fields like `permissions.allow` and `permissions.deny` *merge* across scopes;
scalar fields are overridden by the highest-priority writer.

`CLAUDE_CONFIG_DIR` only redirects the user-global layer; **project-level `.claude/` is
read relative to cwd regardless**.

### Surface 4: Permission system + ENV_SCRUB hardening

This is the surface that has bitten this codebase the hardest, so it gets the most space
in this doc.

The CLI has a permission system documented at
[code.claude.com/docs/en/permissions.md](https://code.claude.com/docs/en/permissions.md):

- **`permissions.allow` / `permissions.deny`**: rule patterns (`Bash(git *)`, `Write`,
  `Edit(*)`, etc.) that gate tool calls.
  Deny wins on first match.
- **`--permission-mode <mode>`**: one of `default`, `acceptEdits`, `auto`,
  `bypassPermissions`, `dontAsk`, `plan`. `bypassPermissions` skips the permission layer
  entirely (except for protected paths).
  Per
  [code.claude.com/docs/en/permission-modes.md](https://code.claude.com/docs/en/permission-modes.md),
  `--dangerously-skip-permissions` is *exactly* `--permission-mode bypassPermissions`.
- **`--allowedTools <tools…>`**: space- or comma-separated allow patterns layered on top
  of settings. Bare `Bash` is equivalent to `Bash(*)`. `Bash(git *)` matches commands
  that start with `git ` (the space enforces word-boundary).
- **`--tools <tools…>`**: orthogonal — controls which built-in tools are *available* to
  the model, not which run without prompts.
- **`--add-dir <dir>`**: extends the agent’s filesystem read/write scope to another
  directory. Independent of `--allowedTools`; required if the agent must touch files
  outside its cwd.

#### ENV_SCRUB: the hardening that overrides bypassPermissions

`CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` is documented at
[code.claude.com/docs/en/env-vars.md](https://code.claude.com/docs/en/env-vars.md) as:
strip Anthropic and cloud-provider credentials from child processes (Bash tool, hooks,
MCP stdio servers); on Linux, also place Bash subprocesses in an isolated PID namespace.
The parent retains credentials for the API call.
Purpose: reduce credential exfiltration risk via prompt injection.

Per the docs, the GitHub Action `claude-code-action` auto-injects this when
`allowed_non_write_users` is configured.
**It is not auto-injected by an outer interactive Claude session.** Our harness sets it
deliberately for Vehicle A: see `credential_scope_env` in claude_code.py — for the
OAuth-token vehicle the slot exports `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` alongside
`CLAUDE_CODE_OAUTH_TOKEN` so the static-bearer credential cannot leak into Bash
subprocesses or MCP children.

The non-obvious side effect: when the inner CLI sees `ENV_SCRUB=1`, it **forces
`permission_mode` back to `default`** and silently ignores both
`--dangerously-skip-permissions` and `--permission-mode bypassPermissions`. It emits the
warning:

> ⚠ Permission mode forced to default — CLAUDE_CODE_SUBPROCESS_ENV_SCRUB is set
> (allowed_non_write_users hardening).
> Declare allowedTools explicitly, or set CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=0 to opt out.

Tracked upstream at
[anthropics/claude-code#51258](https://github.com/anthropics/claude-code/issues/51258)
(feature request to decouple) and
[#46260](https://github.com/anthropics/claude-code/issues/46260) (warning text
confusing). Both open as of 2026-04-30; introduced around v2.1.98-100.

The CLI’s own warning names the two correct workarounds:
1. **Declare `--allowedTools` explicitly** — works *with* the hardening (credentials
   stay scrubbed, only permission gating is bypassed via explicit allow rules).
2. **Set `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=0`** — defeats the credential hardening; only
   acceptable if the harness has alternative credential isolation.

For Vehicle A under metaproc, **option 1 is the correct choice** — credential scrub is
the architecture’s intent; permission allow-listing is what we actually need.

## Design — What the Harness Does

### Slot lifecycle

For each unit of work the runpool acquires a slot.
A slot is a per-attempt directory `<run_dir>/<step>/<item>/.slot/` (the exact shape
lives in the SlotCoordinator).
The slot is the inner CLI’s `CLAUDE_CONFIG_DIR`, and three adapter methods cooperate to
set it up:

1. **`materialize_credential(slot_dir, blob, vehicle=…)`** writes the credential into
   the slot. For Vehicle A (OAUTH_TOKEN) this is a no-op on disk — the static-bearer
   token is injected via env in step 2. The method asserts that no `.credentials.json`
   or `settings.json` exists in the slot, defending against a stray apiKeyHelper
   override that would silently out-vote `CLAUDE_CODE_OAUTH_TOKEN` in the precedence
   chain.

2. **`credential_scope_env(slot_dir, blob, vehicle=…)`** returns the env overrides the
   runpool merges into the subprocess env.
   For Vehicle A: `CLAUDE_CONFIG_DIR=<slot_dir>`, `CLAUDE_CODE_OAUTH_TOKEN=<blob>`,
   `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1`, `DISABLE_UPDATES=1`.

3. **`credential_scrub_env(vehicle=…)`** returns the env vars to *unset* before spawning
   so a higher-precedence credential sitting in the operator’s ambient env can’t
   out-vote the pooled credential.
   Vehicle A keeps `CLAUDE_CODE_OAUTH_TOKEN` (it IS the credential) and scrubs the
   higher-precedence vars *that the harness is willing to override silently*:
   cloud-provider mode flags, `ANTHROPIC_AUTH_TOKEN`, `apiKeyHelper` helper,
   `CLAUDE_CODE_OAUTH_REFRESH_TOKEN`, etc.

   **`ANTHROPIC_API_KEY` is deliberately not scrubbed.** Enterprise API-key mode is an
   explicit operator choice; silently masking it would hide an intentional config from
   the operator. Instead, the slot coordinator refuses slot acquisition when it sees
   `ANTHROPIC_API_KEY` set in the ambient env and emits an explicit warning naming the
   conflict (see `claude_code.py:_compose_slot_env` and the `_refuse_on_ambient_api_key`
   path). This is a separate defense-in-depth mechanism from `scrub_env`: *scrub* removes
   vars that the harness can safely silently override; *refuse* halts the dispatch on
   vars where silent override would mask operator intent.

### Command line construction

`build_command` (the `_build_claude_flags` helper in claude_code.py) assembles the CLI
invocation. The interesting flags for non-interactive runs:

- **`--permission-mode bypassPermissions`** is required to be set in the adapter config.
  Empirically required for non-interactive use in pre-2.1 versions; in 2.1+ it’s
  overridden by `ENV_SCRUB=1` (see above) so it is no longer load-bearing on its own.
  Still set, both for older versions and for clarity of intent.
- **`--dangerously-skip-permissions`** is added when
  `permission_mode=bypassPermissions`. Per docs, this is exactly equivalent to the flag
  above; both being present is redundant but harmless.
  Also overridden by `ENV_SCRUB=1`.
- **`--add-dir <project-cwd>`** extends the agent’s file-access scope to the project
  root so it can write to `<run_dir>/<step>/<item>/...` (which lives outside the slot
  dir). Independent of permissions.
- **`--allowedTools`** with the trusted-dispatch tool set
  (`Bash Write Edit Read Glob Grep WebSearch WebFetch Task TodoWrite NotebookEdit`).
  This is the workaround that survives `ENV_SCRUB=1` and is what unblocks Vehicle A
  non-interactive runs in CLI 2.1.98+.
- **`--no-session-persistence`** keeps the slot from leaking session state between
  attempts.
- **`--strict-mcp-config`** is opt-in via adapter config; refuses unknown MCP server
  names rather than silently ignoring them.

### Why we explicitly allow a wide tool set

The trusted-dispatch tool set
(`Bash Write Edit Read Glob Grep WebSearch WebFetch Task TodoWrite NotebookEdit`) is
broad enough that it is functionally equivalent to bypassing the permission layer for
tool gating. The credential scrub still applies — the inner CLI’s Bash calls don’t see
the parent’s auth env, and the parent’s hooks/MCP children don’t either.
We accept the loss of fine-grained prompt-injection protection in exchange for
unattended-dispatch viability.

If a future use case needs tighter gating (e.g., a research-only slot that should not be
able to write or shell out), the right move is a separate adapter config that passes a
narrower `--allowedTools` list, not adding back-pressure to this default.

## Pitfalls Encountered (and How the Design Avoids Them)

Each row here is a failure mode the harness has actually hit in production or a smoke
run. The “Avoided by” column points at the design element that prevents recurrence; if a
future edit needs to remove that element, it must also explain how the failure mode is
now prevented some other way.

| Pitfall | Symptom | Avoided by |
| --- | --- | --- |
| Stray `apiKeyHelper`-bearing `settings.json` in slot dir silently overrides `CLAUDE_CODE_OAUTH_TOKEN` | Vehicle A run unexpectedly hits Anthropic on a different account; correct token is in env but not used | `materialize_credential` asserts no `.credentials.json` or `settings.json` in slot; raises if found |
| Operator’s ambient `ANTHROPIC_AUTH_TOKEN` (or apiKeyHelper, cloud-provider flag, etc.) out-votes the pooled credential | Slot reports correct fingerprint but actual API call uses operator’s account | `credential_scrub_env` unsets the silently-overridable higher-precedence vars before spawn |
| Operator’s ambient `ANTHROPIC_API_KEY` out-votes the pooled credential (enterprise key mode is an explicit choice that silent override would mask) | As above, but the harness can’t tell whether the operator meant to use enterprise key | Slot coordinator *refuses* acquisition with an explicit warning naming the conflict — distinct from scrub-and-continue |
| Inner Claude inherits parent CLI’s `CLAUDECODE=1` and treats itself as nested-tool | CLI behavior changes (skips startup logging, alters output format) | `prepare_env` strips `CLAUDECODE` before spawn |
| Inner CLI auto-updates mid-cohort, mixing versions across attempts | One attempt runs CLI 2.1.123, the next runs 2.1.124, with classifier surprises | `DISABLE_UPDATES=1` set in `credential_scope_env` |
| Inner CLI persists session state from a previous attempt | Stale conversation context bleeds into the next attempt | `--no-session-persistence` always set |
| Tool calls denied silently because CLI prompts in batch mode (stdin = /dev/null) | Agent reports SUCCESS while writing nothing; `invalid_outputs` failure | `--permission-mode bypassPermissions` is *required* in adapter config; raises ValueError if absent |
| Bash/Write tool calls denied despite `bypassPermissions` because of `ENV_SCRUB=1` override (CLI 2.1.98+) | Cascading `permanent failure [known-bug:claude-startup-exit-1-silent]` across cohort; warning text in attempt log surfaces “Permission mode forced to default — CLAUDE_CODE_SUBPROCESS_ENV_SCRUB is set” | `--allowedTools` flag with the trusted-dispatch tool set; works *with* the credential scrub |
| Agent writes outputs to a path outside the slot’s `CLAUDE_CONFIG_DIR` and the file scope blocks it | Output validator reports “file not found” while the agent reports success | `--add-dir <project-cwd>` extends file scope to the project root |
| `{{run.variant}}` in output path renders as the literal token because the validator gets the wrong variables dict | `output validation failed: ops-review.md: file not found` (file is on disk at the resolved path) | `_execute_agent_step` passes `step_vars` (which sets `VARIANT=effective_variant`) to `validate_item_outputs`, not the run-level variables |

## Failure-Mode Taxonomy

For incident triage, the three classes of failure rooted in this surface:

1. **Credential precedence failure.** Inner CLI uses the wrong account.
   Look for: stray slot files (run `ls <slot_dir>`), surviving env vars (run
   `env | grep -E 'ANTHROPIC|CLAUDE_CODE_OAUTH'` from a slot’s subprocess), apiKeyHelper
   in any settings file in scope.
2. **Permission-gate failure.** Tool calls denied silently.
   Look for the
   `Permission mode forced to default — CLAUDE_CODE_SUBPROCESS_ENV_SCRUB is set` warning
   in the per-attempt JSONL log; if present, `--allowedTools` isn’t being applied (check
   the adapter config and the resolved command).
3. **File-scope failure.** Validator reports file-not-found while the artifact is on
   disk. Almost always a template-render mismatch in the validator’s variables dict;
   check `{{run.variant}}`, `{{run.dir}}`, `{{ticker}}` resolution at the validate site
   vs the build-command site.

## Version Compatibility Matrix (Claude Code CLI 2.1.x)

Compiled 2026-05-23 from upstream release notes and arch validation against the metaproc
claude_code adapter.
Only versions with relevant changes to non-interactive subprocess invocation are listed.
Citations: GitHub
[anthropics/claude-code/releases](https://github.com/anthropics/claude-code/releases).

| Version | Date | `--permission-mode bypassPermissions` honored when ENV_SCRUB=1? | `--allowedTools` workaround functional? | Notes |
| --- | --- | --- | --- | --- |
| v2.1.89 | 2026-04-01 | Yes (ENV_SCRUB not yet introduced) | N/A | Baseline before ENV_SCRUB hardening. |
| **v2.1.98** | 2026-04-09 | **No — ENV_SCRUB forces `default`** | **Yes (introduced as the documented workaround)** | Major security release. `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` introduced; on Linux also PID-namespace isolation for Bash. Permission mode override and warning text introduced. |
| v2.1.110 | 2026-04-15 | No change | Yes | `PermissionRequest` hooks now respect `disableBypassPermissionsMode`. |
| v2.1.113 | 2026-04-17 | No change | Yes | Native-binary migration. Bash deny rules tightened. |
| v2.1.119 | 2026-04-23 | No change | Yes | `--print` honors per-agent `tools:` / `permissionMode`. |
| **v2.1.126** | 2026-05-01 | No change (still overridden by ENV_SCRUB) | **Yes — confirmed working under metaproc harness** | `--dangerously-skip-permissions` scope expanded (`.claude/`, `.git/`, `.vscode/`, shell configs now bypassed). OTEL env vars no longer inherited. |

**Upstream-tracked issues** (open as of 2026-05-23, both with zero Anthropic-team
comments since the arch doc was first written 2026-04-30):

- [anthropics/claude-code#51258](https://github.com/anthropics/claude-code/issues/51258)
  — ENV_SCRUB / permission-mode coupling.
  Filed against v2.1.98.
- [anthropics/claude-code#46260](https://github.com/anthropics/claude-code/issues/46260)
  — confusing override warning.
  Filed against v2.1.100.

The metaproc adapter’s response to ENV_SCRUB hardening (`--allowedTools` with the
trusted-dispatch tool set) **remains the correct upstream-documented workaround through
v2.1.126**. There is no upstream behavior change in any 2.1.99-2.1.126 release that
affects this harness pattern.

## False-Positive Classifier Pitfall

Under heavy auth-pool contention (e.g., 4 parallel Claude orchestrators sharing a
2-label OAuth pool), Claude Code subprocesses can hit 429 rate-limit exhaustion
mid-session and exit with code 1. The harness’s
`metaproc.dispatch.pool_dispatch.classify_failure_for_slot` then prepends the attempt’s
debug-log content (which contains diagnostic tokens like `429`, `rate_limit_error`,
`error`) before the engine error string, then calls the known-bug classifier.

The `claude-startup-exit-1-silent` regex in
[`src/metaproc/dispatch/known_bugs.py`](../../src/metaproc/dispatch/known_bugs.py) uses
forward-only negative lookaheads `(?!.*\d{3,})` and `(?!.*\berror\b)`. Because the
diagnostic tokens are prepended *before* the `exit code 1` match position, the
lookaheads cannot see them, and the regex fires on the rate-limit failure.
Result: `severity=ABORT`, no retry, the work ships as `permanent failure`.

**Symptom on the wrapper-log surface**: cascading
`ticker=X: permanent failure [known-bug:claude-startup-exit-1-silent]` events that
*look* like the ENV_SCRUB issue (because the “Permission mode forced to default —
CLAUDE_CODE_SUBPROCESS_ENV_SCRUB is set” warning is present in the debug log), but the
structured event records `api_status: 429` and the error body contains
`rate_limit_error`.

**Diagnostic checklist** before assuming an ENV_SCRUB problem:

1. Check `events.jsonl` for the affected ticker — is `api_status == 429`?
2. Grep the per-attempt debug log for `rate_limit_error` or `monthly usage limit`.
3. Look at `metaproc auth usage <run-dir>` — is the alt pool saturated?
4. Verify the adapter command line in the `.jsonl.invocation.json` artifact actually
   includes
   `--allowedTools "Bash Write Edit Read Glob Grep WebSearch WebFetch Task TodoWrite NotebookEdit"`.
   If it does, the ENV_SCRUB workaround is in place and the failure has a different
   cause.

**Fix candidates** (open in the runpool design backlog):

- Reverse the prepend order in `classify_failure_for_slot` so diagnostic tokens land
  *after* the `exit code 1` match position, where the lookaheads can exclude them.
  One-line change.
- Add an `api_status == 429` short-circuit between priority 1 (auth signals) and
  priority 2 (known-bug detection) in `classify_failure`. Routes rate-limits to the
  cooling path before the regex has a chance to misfire.
- Tighten the `claude-startup-exit-1-silent` regex with explicit rate-limit exclusion
  (less robust; same lookahead direction problem).

The first fix (reverse prepend order) is the smallest blast radius and is the
recommended action.

## References

- [code.claude.com/docs/en/env-vars.md](https://code.claude.com/docs/en/env-vars.md)
- [code.claude.com/docs/en/permissions.md](https://code.claude.com/docs/en/permissions.md)
- [code.claude.com/docs/en/permission-modes.md](https://code.claude.com/docs/en/permission-modes.md)
- [code.claude.com/docs/en/settings.md](https://code.claude.com/docs/en/settings.md)
- [code.claude.com/docs/en/cli-usage.md](https://code.claude.com/docs/en/cli-usage.md)
- [code.claude.com/docs/en/claude-directory.md](https://code.claude.com/docs/en/claude-directory.md)
- [code.claude.com/docs/en/iam.md](https://code.claude.com/docs/en/iam.md)
- [anthropics/claude-code#51258](https://github.com/anthropics/claude-code/issues/51258)
  — ENV_SCRUB / permission-mode coupling (open)
- [anthropics/claude-code#46260](https://github.com/anthropics/claude-code/issues/46260)
  — confusing override warning (open)
- [arch-authentication.md](./arch-authentication.md) — Vehicle A vs Vehicle B credential
  redesign and pool architecture.
- [adapters/claude_code.py](../../src/metaproc/adapters/claude_code.py) — current
  implementation.

<!-- This document follows std-doc-guidelines.md.
Review guidelines before editing.
-->
