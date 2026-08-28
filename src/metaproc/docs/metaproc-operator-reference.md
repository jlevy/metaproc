---
title: Metaproc Operator Reference
description: The runtime command and recovery reference for operators (human or agent) launching, monitoring, and recovering metaproc workflows.
---
# Metaproc Operator Reference

> **READ THIS FIRST.** If you are an operator agent (human or AI) about to launch,
> monitor, or recover a metaproc workflow, the rules in this doc are not optional.
> They are derived from concrete failures where an operator skipped them and lost hours.
> The CLI surfaces this doc via `metaproc help operator`. If you have NOT read § Top
> mistakes to avoid + § Operating Rules below, stop and read them before touching a run.

Related docs: [concepts](metaproc-concepts.md) (first principles) ·
[developer guide](metaproc-developer-guide.md) (extending metaproc).
This reference and the other bundled docs are served at runtime via
`metaproc help <operator|concepts|developer>`.

## Top Mistakes to Avoid

Treat this as a pre-flight checklist.

| Mistake | Right thing to do |
| --- | --- |
| Parsing run directories with `find`, `ls`, `tail`, or `grep` | Use `metaproc status`, `pulse`, `pool`, `stats`, and `tail --summary`; these commands understand leases and both supported layouts. |
| Treating a low operator cap as a memory-safety control | Set the intended upper bound and let adaptive memory and provider ceilings reduce concurrency from there. |
| Writing an external autopilot or state parser | Add the missing Metaproc command or express the flow as a process spec. |
| Retrying deterministic failures | Adapter classifiers must abort on authentication, installation, version, schema, and configuration failures. |
| Launching without live authentication checks | Run `metaproc auth-check --live --variant <profile>` for every intended profile. |
| Passing environment assignments directly to `caffeinate` | Use `caffeinate ... -- env VAR=value command`; the first argument after `--` must be an executable. |
| Reading macOS free-memory percentage as pressure | Use the runpool pressure events and `metaproc pool status`; reclaimable pages make the raw percentage misleading. |
| Using an aggressive stall timeout for long agent turns | Start with the documented default and change it only after inspecting progress and kill events. |

## Adapter Failure-Classification Contract

**The principle**: a coding-agent CLI (claude / codex / gemini / pi) failing to run is a
failure, exactly like Python failing to start.
The runpool should not paper over it with retries.
Every adapter’s `classify_failure` MUST identify non-retriable errors and return
`AuthFailureClassification(severity=FailureSeverity.ABORT, reason="<specific>")` so the
pool aborts the lane instead of burning a 7-attempt budget on a deterministic config
error.

What MUST escalate to `severity=ABORT` (terminal, no retry — operator action required):

| Class | Signals (any adapter) | Why ABORT |
| --- | --- | --- |
| **Auth / authz** | HTTP 401, HTTP 403, `Expected OAuth2 access token`, `API keys are not supported by this API`, `invalid_grant`, `unauthorized`, `Unauthenticated`, expired credential, missing credential file | Deterministic config error. Retrying doesn’t fix it. |
| **Binary / install** | `command not found`, `No such file or directory: <cli>`, version too old (e.g. `gemini-cli < 0.40` workspace-trust gate exits 55), required Node/Python version not present | The CLI literally cannot run. |
| **Schema / contract** | Adapter returned output that fails validator on every attempt (same error across N attempts), unsupported model name rejected by the CLI’s pre-flight (e.g. `_pi_validate_registration`), known-bug signature match (`metaproc/dispatch/known_bugs.py`) | Software bug or operator misconfig — needs a fix, not retries. |
| **Config / env** | Required env var missing under strict validation, workspace permission denied, sandbox refused, trust-gate not satisfied | Deterministic; will keep failing identically. |
| **Quota — terminal** | “Monthly usage limit reached” with no reset clock, billing failure, account suspended | Cannot recover until operator intervenes. |

What MAY retry (`severity=RETRY_NOW` or `RETRY_AFTER_WAIT`):

- HTTP 429 / `rate_limited` / `Overloaded` (waits + retries)
- HTTP 5xx, connection reset, network errors (immediate retry)
- Stream-idle timeout (likely host suspend, retry on resume)
- Soft validation failure that may flap (only first N attempts; cap then ABORT)

**Implementation requirements** (also called out in the developer guide § Adapter
contract):

- Base class `Adapter.classify_failure` exists in `src/metaproc/adapters/base.py`. The
  default returns `unknown` (→ generic retry).
  Override is currently OPTIONAL — that is the gap that allowed mistake #5 above.
- `claude_code.py` and `codex.py` implement it; `gemini.py` and `pi_cli.py` do NOT.
- Open work: make `classify_failure` REQUIRED (no default fallback to generic retry);
  add the missing implementations for both `gemini.py` and `pi_cli.py`; surface ABORT
  events to the wrapper log with the actual error message (not just `status=exit_N`);
  cascade-abort the whole step after N consecutive ABORTs in one fan-out (suggested
  threshold N=3).

**Operator surfacing**: when ABORT fires, the wrapper log MUST emit the actual error
message in the alternation pattern, not the opaque exit code:

```
# Wrong (current behavior for gemini-cli 401):
Done step=business-setup item=BBAR attempt=4 status=exit_145 (10s)
item=BBAR: retryable [crash] -- retry scheduled (attempt 5, backoff 17s)

# Right (target behavior):
Done step=business-setup item=BBAR attempt=1 status=auth_failed (10s) severity=ABORT
  reason=gemini-401-oauth2-required
  detail: API Error: 401 — API keys are not supported by this API. Expected OAuth2
  access token. (See adapter-compatibility.runbook.md § Gemini auth modes.)
ABORTING step business-setup (cascade: 3 consecutive ABORT in fan-out)
```

Any external monitor filter SHOULD include the tokens
`auth_failed|ABORTING|severity=ABORT|reason=gemini-|reason=pi-|reason=codex-|reason=claude-`
so a content monitor catches this immediately.

Use command help for exact flags:

```bash
uv run metaproc --help
uv run metaproc run-process --help
uv run metaproc trace --help
uv run metaproc pool --help
uv run metaproc auth --help
uv run metaproc gcp --help
```

For procedural how-tos, see the runbooks under
[`runbooks/`](https://github.com/jlevy/metaproc/blob/main/docs/runbooks):

| Runbook | Use when |
| --- | --- |
| [`environment-bootstrap.runbook.md`](https://github.com/jlevy/metaproc/blob/main/docs/runbooks/environment-bootstrap.runbook.md) | First-time setup on a new machine or fresh shell; tool installs; `auth-check` preflight. |
| [`credential-setup.runbook.md`](credential-setup.runbook.md) | Wiring per-adapter credentials (Claude OAuth pool, Codex ChatGPT-plan, Gemini modes, GCP infra + Secret Manager). |
| [`cloud-dispatch.runbook.md`](cloud-dispatch.runbook.md) | Running, monitoring, and recovering jobs on GCP Batch (`--backend gcp-worker`). |
| [`adapter-compatibility.runbook.md`](https://github.com/jlevy/metaproc/blob/main/docs/runbooks/adapter-compatibility.runbook.md) | Adapter-routing pitfalls (pi-cli API matrix, Gemini 3 `thought_signature`, ADC on Batch, `derive_variant` cascade). |
| [`adding-a-new-llm-provider.runbook.md`](https://github.com/jlevy/metaproc/blob/main/docs/runbooks/adding-a-new-llm-provider.runbook.md) | Onboarding a new model or provider into the dispatch matrix. |
| [`softschema-validation.runbook.md`](https://github.com/jlevy/metaproc/blob/main/docs/runbooks/softschema-validation.runbook.md) | Validating softschema-tagged artifacts. |
| [`browser-streaming-smoke.runbook.md`](https://github.com/jlevy/metaproc/blob/main/docs/runbooks/browser-streaming-smoke.runbook.md) | Browser streaming smoke procedure. |

For implementation contracts see [`metaproc-design.md`](metaproc-design.md), for pool
behavior [`arch/arch-runpool.md`](arch-runpool.md), and for naming rules
[`conventions.md`](conventions.md).

## Operating Rules

1. Use metaproc commands before raw filesystem inspection.
   If a normal monitoring question requires `ls`, `tail`, `find`, or an ad hoc parser,
   add or fix a metaproc command instead of encoding a private workflow.
2. Treat `.state/` as harness-owned runtime state.
   Files in `.state/` are full-rewrite or frozen records used for resume, adoption, and
   status checks. Do not hand-edit them except through operator commands such as
   `metaproc override` or `metaproc gcp scale`.
3. Treat `.logs/` as operational logs.
   Files in `.logs/` are append-only source streams, captured stdout/stderr, workflow
   tool streams, or derived JSONL outputs.
   Logs help with trace extraction, debugging, cost, and health analysis, but completion
   state lives in `.state/`.
4. Keep append-only streams single-writer scoped.
   Runpool logs are scoped by step or worker because concurrent pools must not append to
   one shared file. Per-attempt agent logs are scoped by step and item.
5. Treat trace output as derived.
   `metaproc trace --extract` reads source logs and writes `.logs/derived/trace.jsonl`;
   it can be regenerated and should not be edited by hand.
6. Read `steps` and `tasks` as different scopes.
   `steps` means step-runner control-plane data.
   `tasks` means runtime task execution data, including status, attempts, results, and
   agent/session logs.
7. Hold the operator cap high; let the runpool govern down.
   `--max-concurrency` at launch and `pool override --cap N` mid-run set the *operator
   cap*, which is a hand-set ceiling, not the safety governor.
   For a local `run-process`, the launch cap is shared by executable leaves across
   fan-out pools, scalar steps, and composite scopes.
   A `gcp-worker` launch applies it independently inside each worker.
   The adaptive memory and provider ceilings are what actively govern under pressure.
   Command-backed `mode: code` steps also use the shared launch cap and may execute
   concurrently at one DAG level; fan-out paths retain their step caps.
   The run-owned synchronous executor defaults to 32 workers and grows to an explicit
   higher launch cap, so its implementation capacity does not silently lower that cap.
   Code subprocesses share the process directory, so commands that mutate repository
   state, lockfiles, or other shared paths must use per-item paths or their own
   synchronization. For local agent-pool dispatches (claude, codex, gemini, pi-cli), keep
   the operator cap at ≥20 so the adaptive controller has room to ratchet down; setting
   it tighter silently caps
   `effective_target = min(memory_ceiling, provider_ceiling, operator_cap)` with no
   warning, even when the host could safely run more.
   See [`arch-runpool.md`](arch-runpool.md) § “Operator cap floor” for the full
   rationale and why per-adapter memory profiles are not yet stable enough to tune the
   cap tightly.
8. Treat `mode: code` work as owned by the step.
   A command-backed step owns its complete process group.
   Metaproc terminates surviving descendants and flushes the command log before
   releasing run capacity, including after an exit-zero leader.
   Intentional daemonization is therefore unsupported.
   A long-running Python handler under `run-process` must check
   `StepContext.cancel_requested()` at safe checkpoints and return promptly; Metaproc
   waits for started handler work rather than abandoning a thread that may still write
   artifacts.

## Runtime Terms

| Term | Operator meaning |
| --- | --- |
| Step | Authored DAG node in the process spec |
| Step runner | Harness control plane that executes a step, especially a fan-out pool |
| Item | Workflow data record or scalar supplied to a step, often from an items file |
| Task | Runtime execution record for one step applied to one item; scalar steps have one task for the step |
| Attempt | One launch or retry within a task |
| Scope root | A run directory or composite child-process directory with its own `.state/` and `.logs/` |

Use **item** for workflow data and **task** for the harness-owned execution record.
The `tasks/` path segment is about execution state, not the input data by itself.

### Mapped composite scopes

A composite step may declare `for_each` to run one child process per roster item.
The child evaluator runs in the parent process; it does not start a child Metaproc
command or acquire another orchestrator lease.
For item `AAPL` on step `research`, the child scope is `<run>/research/AAPL/` and the
parent task state is `<run>/.state/tasks/research/AAPL/`.

The child process declares every output required for its own valid completion.
The mapped parent separately declares the subset it publishes to downstream steps; the
first implementation does not automatically project child ports.
Metaproc validates both boundaries and revalidates every child-process output before
reusing a completed parent item.
This is stricter than earlier scalar-composite behavior: an inaccurate child output
declaration now fails and must be corrected or removed.
Duplicate resolved item keys fail before execution.

`for_each.max_concurrency` is an optional ceiling on active structural scope evaluators.
It is not a memory estimate or a replacement for executable-leaf and host admission.
Retries belong to child leaves; a whole-scope `for_each.retry` is rejected.
Mapped composites currently run on one host; selecting `gcp-worker` is rejected before
any active DAG step or cloud dispatch begins.
To place a mapped process on one GCP Batch VM, use one `gcp run` task whose command is
`run-process --backend local`; do not chain `gcp run` calls per step or item.

## Starting Runs

Run the full DAG with `run-process`. Most workflow-specific inputs are process
variables, passed with repeated `--var` arguments:

```bash
uv run metaproc run-process path/to/workflow.process.md \
  --var RUNS_DIR=/absolute/path/to/runs \
  --var RUN_ID=<run-id>
```

Useful dispatch selectors:

- `--from <step>` starts at a step and lets downstream dependencies run
- `--only <step>` runs only the named step
- `--skip <step>` marks a step skipped for this invocation
- `--force` bypasses reuse checks throughout the run, including composite descendants
- `--dry-run` prints the plan without launching work

`--skip`, `--from`, and `--only` currently name root-process steps.
They are not matched against same-named steps inside a composite child.

The initial local run-owned pool supports one execution profile per run.
If a later scalar agent leaf resolves to a different profile, Metaproc fails before
launching it; run distinct profiles as separate sibling runs until mixed-profile pool
placement is implemented.

For the common “I edited one step, rerun and reuse the rest” loop, you usually do
**not** pass any of these flags; rerun with the same `RUN_ID` and let the fingerprint
cascade decide. See [§Iterating on a Single Step](#iterating-on-a-single-step).

For exact semantics and current flag names, run:

```bash
uv run metaproc run-process --help
uv run metaproc plan --help
uv run metaproc deps --help
```

### Domain Dispatch Pattern

Metaproc should receive concrete run invocations, not domain-specific selection logic.
For daily or recurring domain workflows, keep roster selection, tiering, source-health
policy, and reporting in the client package playbook or CLI, then compile that intent to
ordinary metaproc commands.

For example, a downstream daily-batch playbook can own branch setup, roster generation,
preflight, source-health gates, launch commands, monitoring, salvage, and rollup.
The generated daily plan or report should link that playbook instead of restating the
full operating procedure.
Domain workflows should keep routine runtime defaults in process frontmatter, usually
`process.defaults.default_execution_profile` plus input defaults such as `runs_dir` and
`artifact_namespace`. Use `--variant <profile>` only when a run intentionally overrides
that process default.

The reusable metaproc command set is:

| Need | Command |
| --- | --- |
| Validate process headers | `uv run metaproc check-headers <process-dir>` |
| List execution profiles | `uv run metaproc variants <process>` |
| Auth/capacity preflight | `uv run metaproc auth-check --live --variant <profile>` |
| Launch the DAG | `uv run metaproc run-process <process> --var KEY=value ...` |
| Launch with profile override | `uv run metaproc run-process <process> --variant <profile> --var KEY=value ...` |
| Check progress | `uv run metaproc status <run-dir>` |
| One-line health pulse (orch/progress/auth) | `uv run metaproc pulse <run-dir>` |
| Check pool pressure | `uv run metaproc pool status <run-dir>` |
| Read pool events | `uv run metaproc pool events <run-dir>` |
| Tail summarized task logs | `uv run metaproc tail <run-dir>/.logs --once --summary` |
| Build/query trace health | `uv run metaproc trace --extract <run-dir>` then `uv run metaproc trace --health <run-dir>` |
| Stop a local run | `uv run metaproc kill <run-dir>` |

If a domain operator needs a recurring status field that is not available through these
commands, add it to the domain report or to metaproc.
Do not make a daily plan depend on private shell walks through `.logs` or `.state`.

For local laptop runs, sleep can present as a per-item stall.
Check `metaproc status` first: if it shows the item retrying, let the retry run; if the
orchestrator exited, restart the same launch command with the same `RUN_ID` so completed
steps can be reused.
Create a new run ID only when a clean duplicate run is intentional.

## Iterating on a Single Step

This is the routine “I edited one step, rerun the process and reuse everything else”
loop. The shape is always the same: rerun `run-process` with the **same `RUN_ID` and
`RUNS_DIR`** as the prior run.
The orchestrator decides what to reuse from fingerprints; you do not pass
`--from`/`--force` for the common case.

### Recipe

1. **Find the prior RUN_ID** if it is not already in your shell.
   Run dirs live under `RUNS_DIR/<RUN_ID>/`; the most recent is usually the target.
   `metaproc status` accepts either a run-dir path or a bare run-id.

2. **Preview what will rerun** before launching:

   ```bash
   uv run metaproc status <run-dir-or-run-id> --steps --stale-only
   ```

   Rows in `stale` or `invalidated` will rerun on the next launch; their descendants
   will rerun too even though they still show as `current` (see the cascade note below).

3. **Edit the step’s runbook** (or change inputs / adapter config covered by the step
   contract).

4. **Rerun with the same `RUN_ID`**. Upstream stays cached; the edited step and its
   descendants re-execute.

### When fingerprint reuse fires

Each completed step records a 16-character fingerprint covering the step’s contract (id,
declared inputs/outputs, adapter config) plus the bytes of any runbook file referenced
via `prompt_paths` or composite `uses`. On rerun against the same `RUN_ID`, the
orchestrator notices the fingerprint flip and:

1. Treats the edited step as not-completed even though its outputs still exist.
2. Renames downstream `status.yaml` files to `status.yaml.stale` so descendants
   re-execute too. The cascade is automatic; no `--from` or `--force` needed.
3. Leaves upstream steps cached as long as *their* runbooks are unchanged.

The cascade only fires when the recorded fingerprint disagrees with the current one.
Runs whose completion records carry no `recorded_step_hash` (legacy completions) are
kept as completed and are *not* re-executed.

### When to reach for a flag

The fingerprint covers runbook bytes and the declared step contract.
It does **not** cover Python handler code.
Use a flag when the change is invisible to the fingerprint or you want to override its
decision:

| Situation | Flag |
| --- | --- |
| Pure runbook / prompt edit | none — rerun with same `RUN_ID` |
| Edited a `mode: code` handler (fingerprint-blind) | `--from <step>` |
| Want to rerun only one step in isolation, ignore the cascade | `--only <step>` |
| Skip a step you know is fine, override caching | `--skip <step>` |
| Force a rerun the fingerprint thinks is unnecessary | `--force` |

Use `run-process --dry-run` or `metaproc deps <run>` to preview the cascade if you are
unsure what the next launch will execute.

Keep every resolved `--var` value unchanged when resuming a run ID. Metaproc rejects a
changed, added, or removed variable before it reuses task state; start a new run ID for
a different input set.
Equivalent local and cloud Filestore mount aliases for `RUNS_DIR` are the sole
normalization exception.

### Worked example

```bash
# Initial run — every step executes.
uv run metaproc run-process workflows/research/research.process.md \
  --var RUNS_DIR=/absolute/path/to/runs \
  --var RUN_ID=demo

# Edit one referenced runbook.
$EDITOR workflows/research/analyze.runbook.md

# Optional: preview what will rerun.
uv run metaproc status /absolute/path/to/runs/demo --steps --stale-only

# Resume the same RUN_ID. Unchanged ancestors stay cached; the edited
# step and its descendants re-execute.
uv run metaproc run-process workflows/research/research.process.md \
  --var RUNS_DIR=/absolute/path/to/runs \
  --var RUN_ID=demo
```

## Adapters and Settings

Use `metaproc variants <process>` to list named execution profiles, their adapter,
capabilities, resource hints, and auth readiness.
`--variant <name>` remains the operator-compatible selector for an execution profile.
The `Notes` column is an operator hint for routine use; capability and auth columns are
the mechanical preflight signals.
If the process declares `process.defaults.default_execution_profile`, `run-process` uses
that profile when `--variant` is omitted.
Keep routine defaults in process frontmatter and use `--variant` for explicit operator
overrides or comparison runs.

```bash
uv run metaproc variants <process.process.md>
uv run metaproc run-process <process.process.md> \
  --var RUN_ID=<run-id>
uv run metaproc run-process <process.process.md> \
  --variant <execution-profile> \
  --adapter-config effort=high
```

`run-process --variant` selects `run.execution_profile`. `--artifact-namespace` selects
`run.artifact_namespace`; if omitted, it defaults to an authored
`ARTIFACT_NAMESPACE`/`VARIANT` process input when present, then to the selected
execution profile. `{{run.variant}}` is only a migration alias for
`{{run.artifact_namespace}}`.

Preflight credentials before a live dispatch:

```bash
uv run metaproc variants <process.process.md>
uv run metaproc auth-check --live --variant <execution-profile>
```

For Codex, `OPENAI_API_KEY` is an API-platform credential and uses API billing.
It does not consume the ChatGPT Pro Codex allowance.
To use the ChatGPT-plan Codex allowance, authenticate the CLI with `codex login`; for
remote workers, materialize that OAuth credential through the `codex-auth` Secret
Manager flow below.

For Codex OAuth credentials that need to be pre-authenticated for workers, use the
`codex-auth` surface:

```bash
uv run metaproc codex-auth show
uv run metaproc codex-auth push
```

`codex-auth push` requires `~/.codex/config.toml` to contain
`cli_auth_credentials_store = "file"` and `~/.codex/auth.json` to exist from a fresh
`codex login`. If a pooled OAuth Codex run is used, also check ambient-key conflicts:

```bash
uv run metaproc auth env <process.process.md>
```

An `OPENAI_API_KEY` in the shell or auto-loaded `.env` intentionally fails that pooled
OAuth check because it would override the scoped Codex credential.

## Local, Cloud, and Worker Execution

Local runs use the default local backend.
For compatible multi-VM fan-out, use `--backend gcp-worker --cloud` or the workflow’s
cloud-oriented wrapper options.
The bare `gcp-worker` backend is reserved for the inner Batch orchestrator leg.
A complete local-backend DAG may instead run as the one command in a `gcp run` Batch
task. That form keeps one host and is the current cloud placement for mapped composites;
the nested `run-process` remains the only DAG orchestrator.
Use command help for the current flag set:

```bash
uv run metaproc run-process --help
uv run metaproc gcp run --help
uv run metaproc gcp status --help
uv run metaproc gcp logs --help
uv run metaproc gcp scale --help
```

Operationally, a cloud run has the same run directory layout as a local run.
The orchestrator writes run-level state and per-step runner state.
Workers write worker-scoped state under `.state/workers/<worker-id>/` and worker-scoped
runpool events under `.logs/runpool/workers/<worker-id>/events.jsonl`.

## Credential Pools

Credential pools let fan-out steps lease labeled credentials instead of using one
ambient credential for every item.
Use the `auth` subcommands to inspect and maintain labels:

```bash
uv run metaproc auth status
uv run metaproc auth list --adapter claude-code-cli
uv run metaproc auth probe claude-code-cli <label>
uv run metaproc auth usage <run-dir>
uv run metaproc auth doctor --run-dir <run-dir>
```

Wire a run to a specific account pool with `run-process` auth flags:

```bash
uv run metaproc run-process <process.process.md> \
  --auth-account claude-code-cli \
  --auth-include-labels alt1 \
  --auth-include-labels alt2
```

Use `uv run metaproc run-process --help` for the full credential-pool flag set (the
underlying CLI flags are named `--auth-*`), including fallback and preflight behavior.

The configured pool applies to matching scalar and fan-out agent steps.
A step using a different adapter continues with that adapter’s ambient authentication
and emits a warning naming both adapters.
Treat that warning as evidence that the step is outside the pool, especially when
comparing pool usage with expected task counts.

With `--backend gcp-worker`, scalar agent steps execute on the orchestrator while
fan-out items execute on workers.
Both lease from the same configured label set, so a long scalar call can hold a label
that a worker is also waiting to acquire.
Size the label set for the combined orchestrator-and-worker demand and use `auth usage`
plus `pool events` to inspect contention.

## Monitoring Commands

| Question | Command |
| --- | --- |
| Is the run still alive? | `uv run metaproc status <run-dir-or-run-id>` |
| Is this run up to date with the current process? | `uv run metaproc status <run> --steps` |
| Which steps are stale or already invalidated? | `uv run metaproc status <run> --steps --stale-only` |
| Wait until the run finishes | `uv run metaproc wait <run-dir-or-run-id>` |
| What is happening in agent logs? | `uv run metaproc tail <run-dir>/.logs --once --summary` |
| What did the DAG do? | `uv run metaproc trace --extract <run-dir> && uv run metaproc trace <run-dir> --tree` |
| What failed? | `uv run metaproc trace --extract <run-dir> && uv run metaproc trace <run-dir> --health` |
| What did it cost? | `uv run metaproc trace --extract <run-dir> && uv run metaproc trace <run-dir> --cost` |
| How did concurrency change? | `uv run metaproc pool concurrency-timeline <run-dir>` |
| What did the pool record? | `uv run metaproc pool events <run-dir>` |
| What did every run-owned and step pool record? | `uv run metaproc pool rollup <run-dir>` |
| What are throughput and resource totals? | `uv run metaproc stats <run-dir>` |
| Which auth labels were used? | `uv run metaproc auth usage <run-dir>` |
| What is the cloud Batch state? | `uv run metaproc gcp status <run-id>` |
| What did cloud jobs log? | `uv run metaproc gcp logs <run-id>` |

For a live run, prefer `status`, `wait`, `tail`, `pool`, `auth usage`, `stats`, and
`gcp` commands over raw `tail`, `find`, Cloud Logging queries, or private parsers.

### Steps section in `metaproc status`

`status` renders a per-step Steps table whenever it can rebuild the resolved plan from
`<run>/.state/run-config.yaml`. The table shows each step’s `StepState` plus its
`recorded → current` fingerprint:

- `current` — completed, fingerprint matches the current process definition.
- `stale` — completed, but the step’s runbook or contract changed since the last
  completion. Re-running against this RUN_ID will re-execute the step plus its
  downstream.
- `invalidated` — a prior `status.yaml` was renamed `.stale` by `--force` or by the
  fingerprint cascade.
  The step will rerun.
- `missing` — never started, or started and failed without a recorded completion.
- `in_flight` — actively running.

Above the table, a one-line summary tells you whether anything needs attention:

- `Process: current` — every step is `current` or `missing`.
- `Process: stale (N steps need rerun)` — at least one step is `stale` or `invalidated`.
  `N` counts those two states (running steps are excluded — the orchestrator is already
  on them).

The top `Status:` label reports execution, while `Process:` reports definition
freshness. A terminal code-step failure therefore renders `Status: FAILED` with its
durable task error and does not render the potentially misleading `Process: current`
summary. Full JSON output exposes these facts as `process_execution_state` and
`process_error`; the projected `--steps` JSON surface remains
`{run_dir, process_state, steps}`.

Useful flags:

- `--steps` shows only the Steps table (skips the variant table, timing, system metrics,
  overrides, and auth pool sections).
  In JSON mode this also projects the payload down to `{run_dir, process_state, steps}`
  so scripts don’t have to pick through variant/timing/system noise.
- `--stale-only` filters the Steps table to rows that are already `stale`,
  `invalidated`, or `in_flight`. The same filter applies to JSON output.
  Note that this lists steps whose recorded state currently disagrees with the process
  definition; it does *not* project the cascade — descendants of a stale step will rerun
  on the next launch but only become `invalidated` once their `status.yaml` is renamed
  `.stale`. To preview the cascade, run `metaproc deps <run>` or invoke
  `run-process --dry-run`.

When `run-config.yaml` is missing, the spec has moved, or the plan no longer builds
under the captured params, the Steps section is omitted silently — the rest of
`metaproc status` still works because execution state comes directly from
`process-status.yaml`. `status --check` and `wait` treat a terminal process failure as
failed even when the process had no fan-out items.

## Log Compression

Keep compression separate from live preflight and correctness validation.
The normal pattern is to let `run-process` surface its terminal status first, then run
compression as a cleanup track in another terminal or after the run is no longer
expected to resume. This prevents disk maintenance from hiding workflow failures or
delaying the operator’s first answer on whether the run itself worked.

Use these primitives for routine cleanup and retroactive disk reclamation:

```bash
uv run metaproc compact-logs <run-dir> --adapters pi,codex --min-size 131072
uv run metaproc gzip-text <run-dir>/.logs/tasks --include .jsonl,.log --min-size 131072
```

The compaction pass targets noisy adapter streams.
The gzip pass above intentionally points at `.logs/tasks`, where per-attempt files are
closed once the attempt exits.
Broader ad hoc sweeps can include runpool and tool logs, but only run them when you are
done resuming that run.

For old completed workflow text blobs, use an explicit artifact sweep rather than
changing the automatic log policy:

```bash
uv run metaproc gzip-text <old-run-dir>/artifacts \
  --include .html,.md \
  --min-size 262144
```

Metaproc readers and metabrowser treat `.jsonl.gz`, `.log.gz`, and other gzipped text
files as their logical uncompressed types, so compressed logs remain inspectable through
the normal status, tail, trace, stats, and browser surfaces.

## Stopping Runs

Use Metaproc stop surfaces instead of raw process kills.
Manual `kill <PID>` and `pkill -f ...` can leave stale orchestrator leases, active
credential leases, or child worker processes that the next resume has to untangle.

For local runs:

```bash
uv run metaproc kill <run-dir>
```

`metaproc kill` handles both local stop windows:

- if a fan-out pool has initialized, it writes the pool kill sentinel and signals active
  worker subprocesses
- if the run is still in an early code or bundle stage before the first pool exists, it
  targets the same-host orchestrator lease owner and clears the lease after termination

Useful options:

- `--drain` stops new pool launches and lets active workers finish
- `--force` escalates to `SIGKILL`
- `--variant <name>` limits a pool stop to matching worker labels
- `--format json` returns a machine-readable result

For cloud runs:

```bash
uv run metaproc gcp cancel <run-id>
```

After a stop, verify through Metaproc:

```bash
uv run metaproc status <run-dir> --format json
uv run metaproc pool status <run-dir>
```

`status` should report `is_active: false`. `pool status` may legitimately say no pool
status file exists if the run stopped before fan-out started.

## Runtime Layout

Operator-facing summary of where to look for a running or completed run.
For the full per-artifact reference (schema, lifecycle, writer, readers), see
[artifact-catalog.md](artifact-catalog.md).

Each scope root has two reserved runtime branches and any number of workflow artifact
branches:

```text
<scope-root>/
  .state/          harness state used for resume, adoption, and status
  .logs/           operational logs, source streams, captured output, derived JSONL
  <artifacts...>   workflow outputs declared by the process spec
```

New runs write `metaproc_layout: metaproc-run-layout/2` in `.state/run-config.yaml`.
Readers use that marker to choose the V2 layout and use exact legacy fallback paths only
for unmarked old runs.

### State Files

| Artifact | Current path | Meaning |
| --- | --- | --- |
| Run config | `<run>/.state/run-config.yaml` | Frozen run identity, variables, and layout marker |
| Orchestrator lease | `<run>/.state/orchestrator-lease.yaml` | Owner and heartbeat for cross-host safety |
| Process status | `<run>/.state/process-status.yaml` | Aggregated DAG state for status display |
| Overrides | `<run>/.state/overrides.yaml` | Operator dependency overrides |
| Step runner state | `<run>/.state/steps/<step_id>/` | Runpool status, scale state, dispatch manifest, claimed items |
| Worker runner state | `<run>/.state/workers/<worker-id>/` | Worker-scoped runpool status for cloud or worker fan-out |
| Task state | `<run>/.state/tasks/<step_id>/<item_key>/` | Current attempt, status, result, and manual acknowledgments |
| Scalar task state | `<run>/.state/tasks/<step_id>/` | State for non-fan-out steps with exactly one task |

### Log Files

| Artifact | Current path | Meaning |
| --- | --- | --- |
| Process events | `<run>/.logs/process-events.jsonl` | Run-level DAG lifecycle events |
| Dispatch config changes | `<run>/.logs/dispatch-config-changes.jsonl` | Append-only record of live dispatch config edits |
| Step runpool events | `<run>/.logs/runpool/steps/<step_id>/events.jsonl` | Per-step fan-out runner events |
| Worker runpool events | `<run>/.logs/runpool/workers/<worker-id>/events.jsonl` | Worker-scoped runner events |
| Agent session logs | `<run>/.logs/tasks/<step_id>/<item_key>/*.jsonl` | Per-attempt adapter stream JSONL |
| Captured process output | `<run>/.logs/tasks/<step_id>/process_<ts>.log` | Scalar code/subprocess stdout and stderr |
| Captured item output | `<run>/.logs/tasks/<step_id>/<item_key>/process_<ts>.log` | Item-scoped code/subprocess stdout and stderr |
| Workflow tool logs | `<run>/.logs/tools/<tool-name>/invocations.jsonl` | Workflow-owned tool invocation streams |
| Trace output | `<run>/.logs/derived/trace.jsonl` | Derived `TraceEvent/0.1` output from `metaproc trace --extract` |

Runpool event streams include `auth_lease_acquired` and `auth_outcome` when a pooled
credential is used.
An `auth_skipped` event with `pool_enabled: false` records an adapter
mismatch that used ambient authentication instead, so pool use can be audited without
scraping console output.

`tools/<tool-name>/` marks workflow ownership even though the file is operationally a
log. `derived/` marks extractor output; trace extractors should not treat it as source
input.

## Trace Workflow

`metaproc trace --extract <run-dir>` reads source logs and writes
`.logs/derived/trace.jsonl`. The trace views read that derived file:

```bash
uv run metaproc trace --extract <run-dir>
uv run metaproc trace <run-dir> --table
uv run metaproc trace <run-dir> --tree
uv run metaproc trace <run-dir> --health
uv run metaproc trace <run-dir> --cost
```

Extraction from a parent run includes framework logs from nested composite scope roots.
Every span carries `scope.path`; `.` identifies the parent and paths such as
`research/AAPL` identify child scopes.
Nested span IDs and cross-source joins are scoped, so repeated child step and item names
do not collide. If a Gemini attempt finishes successfully after a failed tool call, the
tool remains an `error` span with `error.recovered: true` for diagnosis but does not
change the successful session or attempt status.

`pool rollup` follows the same composite-scope discovery rule.
It includes a scope’s run-owned root pool at `.` or its relative scope path, plus any
step-owned pools below that scope.

Re-run extraction after a run completes, after recovering old logs, or after changing an
extractor. Do not edit trace JSONL by hand; fix the source log or extractor and
regenerate it.

## Old Runs

Old run directories are not rewritten.
For unmarked runs, readers fall back to exact legacy paths such as
`.logs/steps/<step>/runpool-events.jsonl`, `.logs/worker-<id>/runpool-events.jsonl`,
`.state/worker-<id>/runpool-status.yaml`, `.logs/trace.jsonl`, and
`.logs/arena-tools.jsonl`. New runs should not write those paths.

If a current command cannot read an old run, fix the shared path helper or command
reader rather than adding a one-off parser.

## Design Sync

When runtime paths change, update these documents in the same PR:

- this operator reference
- [artifact-catalog.md](artifact-catalog.md)
- [conventions.md](conventions.md)
- [metaproc-design.md](metaproc-design.md)
- [arch-runpool.md](arch-runpool.md)
- [../README.md](https://github.com/jlevy/metaproc/blob/main/README.md)
- active specs that name source log paths
- workflow runbooks or process specs that pin tool-specific environment variables

The operator reference owns command sequences and current paths.
Design docs should explain the underlying contracts and link here for operator flows.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
