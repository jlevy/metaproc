# Metaproc Orchestration Patterns

Metaproc documents itself, and this skill is orchestration glue + routing only — it does
not restate the manuals.
**Before you run, monitor, resume, or debug any metaproc process, read the operator
manual first — every time:**

```
metaproc help operator
```

The operator manual covers how to start, monitor, resume, and stop runs; `status` /
`trace` / `pool` / `tail` / `stats`; the runtime layout; and the operating rules.
If you do not know how to do something with metaproc, the answer is in
`metaproc help operator` or `metaproc <command> --help` — find it there before
improvising.
`metaproc help developer` covers extending metaproc and the “metaproc is the
wrapper” policy; `metaproc help concepts` covers the process model (composite mode,
fan-out, step fingerprints, the resume model).
The full topic list is at the end of this skill.

## Do Not Improvise Around Metaproc

The most common failure mode is an agent that skipped the operator manual and hand-rolls
what metaproc already does:

- **Never inspect a run with ad-hoc bash** — no `ls` / `tail` / `find` / `grep` / `jq`
  over the run dir, `.state/`, or `.logs/`. Use `metaproc status` / `wait` / `tail` /
  `pool …` / `trace` / `stats` / `auth usage` / `gcp …`. The operator manual’s
  **Monitoring Commands** table maps every question to its command (Operating Rule 1). A
  monitoring question that seems to need raw bash is the signal to add or fix a metaproc
  command — not to write the script.
- **Never wrap metaproc** in a Python or shell orchestrator.
  A multi-step flow is a `*.process.md`; a new run-state view is a `metaproc`
  subcommand. See `metaproc help developer`.
- **Env hygiene for long runs** (`env -u ANTHROPIC_API_KEY`; wrapper log on persistent
  storage, never `/tmp/`; `caffeinate`): `metaproc/docs/conventions.md` Logging Rules
  and `docs/general/guidelines/agent-orch-guidelines.md`.

## When to Invoke

- Launching or resuming a `metaproc run-process` (a composite-parent batch or any
  process)
- Running a single step in isolation via `metaproc run-step`
- Arming autonomous supervision with `metaproc pulse`
- Deciding whether new functionality belongs in metaproc, a workflow helper, or a skill

Workflow-specific skills (such as the EIA-batch skill) delegate here for the kickoff
sequence and defer to the metaproc CLI for execution.

## Kickoff Sequence: Preflight → Confirm → Launch → Supervise

1. **Preflight.** Run the workflow’s preflight step alone to surface a GO/WARN/NO-GO
   before committing the full batch:
   `metaproc run-step <process.md> --step <preflight-step> --wait --var RUN_ID=<id> ...`.
   The non-obvious part this sequence relies on: the completed step is recorded in the
   run dir, so the later `metaproc run-process` (same `RUN_ID`) sees it done via its
   fingerprint and resumes past it (resume model: `metaproc help concepts`). The
   preflight step’s probes and verdict live in the workflow package, not in metaproc.
2. **Confirm.** Present the preflight verdict + kickoff summary via `AskUserQuestion` —
   one gate, no follow-up prompts.
   The workflow skill composes the payload.
3. **Launch.** `metaproc run-process <process.md> --variant <profile> --var ...` resumes
   past the completed preflight step.
   Flags: `metaproc run-process --help`. Launch each execution profile as a separate
   background process; the launch-command *shape* (env hygiene, wrapper-log piping) is
   the workflow playbook’s § Launch Command.
4. **Supervise.** Hand `metaproc pulse <run-dir>` to Monitor/CronCreate so you are
   notified of completion, stalls, or failures without polling — and keep reading the
   operator manual’s Monitoring Commands for anything `pulse` does not cover.
