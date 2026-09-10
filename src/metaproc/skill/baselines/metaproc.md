# Metaproc Orchestration Patterns

This skill routes orchestration work to Metaproc’s bundled manuals and CLI. It does not
restate the manuals.
Before you run, monitor, resume, or debug a Metaproc process, read the operator manual:

```
metaproc help operator
```

The operator manual covers how to start, monitor, resume, and stop runs; the `status`,
`trace`, `pool`, `tail`, and `stats` commands; the runtime layout; and the operating
rules. If you do not know how to do something with metaproc, the answer is in
`metaproc help operator` or `metaproc <command> --help`. Check those sources before
improvising.
`metaproc help developer` covers extending metaproc and the “metaproc is the
wrapper” policy; `metaproc help concepts` covers the process model (composite mode,
fan-out, step fingerprints, the resume model).
The full topic list is at the end of this skill.

## Do Not Improvise Around Metaproc

The most common failure mode is an agent that skipped the operator manual and hand-rolls
what metaproc already does:

- **Use Metaproc commands for run state and lifecycle:** `status`, `wait`, `tail`,
  `pool`, `trace`, `stats`, `auth usage`, or `gcp`. Do not replace their orchestration
  or state-mutation contracts with a private script.
- **Inspect captured agent logs directly when debugging.** Read or tail the relevant
  attempt’s captured output and available native transcript, using a viewer or read-only
  shell tools as needed.
  Some captures merge stdout/stderr or filter native events; Metaproc rollups and
  extracted traces can omit further evidence.
  The operator manual’s **Direct Agent Debugging** section explains the source paths.
  Report the cause, affected attempt, and evidence location; if the cause or logs are
  unavailable, say what is missing.
  File a missing diagnostic as a Metaproc issue while continuing the investigation from
  the retained source evidence.
- **Do not wrap metaproc** in a Python or shell orchestrator.
  A multi-step flow is a `*.process.md`; a new run-state view is a `metaproc`
  subcommand. See `metaproc help developer`.
- **Env hygiene for long runs** (`env -u ANTHROPIC_API_KEY`; wrapper log on persistent
  storage, never `/tmp/`; `caffeinate`): see `metaproc help operator` and the
  repository’s `docs/conventions.md`.

## When to Invoke

- Launching or resuming a `metaproc run-process` (a composite-parent batch or any
  process)
- Running a single step in isolation via `metaproc run-step`
- Arming autonomous supervision with `metaproc pulse`
- Deciding whether new functionality belongs in metaproc, a workflow helper, or a skill

Workflow-specific skills (such as the large workflow-batch skill) delegate here for the
kickoff sequence and defer to the metaproc CLI for execution.

## Kickoff Sequence: Preflight → Confirm → Launch → Supervise

1. **Preflight.** Run the workflow’s preflight step alone to surface a GO/WARN/NO-GO
   before committing the full batch:
   `metaproc run-step <process.md> --step <preflight-step> --wait --var RUN_ID=<id> ...`.
   The non-obvious part this sequence relies on: the completed step is recorded in the
   run dir, so the later `metaproc run-process` (same `RUN_ID`) sees it done via its
   fingerprint and resumes past it (resume model: `metaproc help concepts`). The
   preflight step’s probes and verdict live in the workflow package, not in metaproc.
2. **Confirm.** Present the preflight verdict and kickoff summary through the agent
   environment’s user-confirmation mechanism.
   Use one gate with no follow-up prompts.
   The workflow skill composes the confirmation payload.
3. **Launch.** `metaproc run-process <process.md> --variant <profile> --var ...` resumes
   past the completed preflight step.
   Flags: `metaproc run-process --help`. Launch each execution profile as a separate
   background process; the launch-command *shape* (env hygiene, wrapper-log piping) is
   the workflow playbook’s § Launch Command.
4. **Supervise.** Use the agent environment’s monitoring or scheduling mechanism to run
   `metaproc pulse <run-dir>` and notify you of completion, stalls, or failures without
   manual polling. Use the operator manual’s Monitoring Commands for anything `pulse`
   does not cover.
