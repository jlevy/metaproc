# Metaproc

Generic process framework for structured multi-step agent workflows.

Metaproc executes dependency-aware process specs through local code handlers or agent
CLI adapters.
It provides planning, fan-out, resume, validation, status, trace, resource,
credential-pool, and optional GCP Batch primitives while leaving domain schemas,
prompts, handlers, and policies to downstream packages.

## Features

- Markdown process specs with YAML frontmatter, typed inputs, dependencies, steps,
  outputs, fan-out, and composite processes
- Local Python and shell handlers plus Claude Code, Codex, Gemini, and pi CLI adapters
- Resumable DAG execution with fingerprints, completion validation, overrides, and
  structured `.state` and `.logs` artifacts
- RunPool concurrency, retry, resource-pressure, credential-pool, and ledger-backed
  resource observability controls
- Optional GCP Batch dispatch and Secret Manager integration
- A packaged Metabrowser plugin for process specs, plans, traces, logs, and resource
  reports
- Self-documenting: bundled manuals via `metaproc help` and a generated portable Agent
  Skill installed by `metaproc skill`

## Installation

Metaproc requires Python 3.12 or newer and uses [uv](https://docs.astral.sh/uv/). Run an
exact release without a persistent installation:

```shell
uvx metaproc@0.2.1 --help
```

For a persistent tool installation:

```shell
uv tool install metaproc
metaproc --help
```

Install the optional local browser integration with
`uv tool install 'metaproc[browser]'`. Cloud dependencies are similarly isolated in the
`gcp` and `gcp-batch` extras.

Metaproc currently supports Linux and macOS. Its process-control and resource-monitoring
features require a POSIX operating system.

See [installation](docs/installation.md) for source-checkout, upgrade, and Agent Skill
instructions.

## Quickstart

Run the deterministic source-checkout example without an agent CLI, network call, or
cloud credential:

```shell
make install
uv --config-file uv.toml run --frozen metaproc run-process \
  examples/offline-smoke/offline-smoke.process.md \
  --var RUNS_DIR="$(pwd)/.runs" \
  --var RUN_ID=quickstart
```

The process writes three outputs and its structured run state beneath
`.runs/quickstart/`. Re-running it demonstrates completion caching; pass `--force` to
execute every step again.

Client process specs may require additional variables before execution.
The most common one is `RUNS_DIR`: an absolute path used to template output locations
via `{{run.parent_dir}}`. Workflow launchers resolve `RUNS_DIR` from settings they own
and pass the absolute value into `run-process` / `run-step`; Metaproc stays
workflow-agnostic and does not synthesize it.
The [offline example](examples/offline-smoke/offline-smoke.process.md) passes the value
explicitly. Template variables are case-sensitive; see
[conventions](src/metaproc/docs/conventions.md) for the casing rules and the small set
of framework built-ins.

## Documentation

Metaproc documents itself: every document below ships inside the package, and the
command shown with each one prints it in the terminal.
Run `metaproc help` alone for the full topic list with sizes.
Each document is also linked directly, so nothing here requires the CLI to read.
Documentation for working on Metaproc itself lives in the repository, not the package;
see [Project Docs](#project-docs).

### Essential Docs

Read these in order:

1. **[Metaproc Concepts](src/metaproc/docs/metaproc-concepts.md)**
   (`metaproc help concepts`): the vocabulary, the ownership boundaries, the four step
   modes, and the optimization loops.
   Every other document assumes this one.
2. **[Metaproc Design](src/metaproc/docs/metaproc-design.md)** (`metaproc help design`):
   how the system is built, covering the spec format, resolved plans, runtime artifacts,
   resumability, the adapter contract, and the robustness subsystems.
3. **[Metaproc Operator Reference](src/metaproc/docs/metaproc-operator-reference.md)**
   (`metaproc help operator`): the runtime CLI reference for starting, monitoring,
   resuming, and stopping runs.

### Developer and Architecture Docs

For developers extending Metaproc, from building workflows on it to its subsystem
internals. All of these ship in the package:

- **[Metaproc Developer Guide](src/metaproc/docs/metaproc-developer-guide.md)**
  (`metaproc help developer`): for developers building workflows *on* Metaproc with
  process specs, handlers, and plugins, and the “Metaproc is the right wrapper” policy.
  Working on Metaproc itself is covered under [Project Docs](#project-docs) instead.

- [arch-authentication](src/metaproc/docs/arch-authentication.md)
  (`metaproc help arch-auth`): credential pools, adapter auth modes, secret refs, and
  Secret Manager hydration.

- [arch-cloud-execution](src/metaproc/docs/arch-cloud-execution.md)
  (`metaproc help arch-cloud`): GCP Batch dispatch, orchestrator and worker placement,
  logs, and recovery.

- [arch-runpool](src/metaproc/docs/arch-runpool.md) (`metaproc help arch-runpool`): the
  local agent process manager, covering adaptive concurrency, memory pressure, and host
  coordination.

- [arch-claude-code-harness](src/metaproc/docs/arch-claude-code-harness.md)
  (`metaproc help arch-harness`): the Claude Code adapter harness and its wire format.

- [arch-execution-model](src/metaproc/docs/arch-execution-model.md)
  (`metaproc help arch-execution`): how the execution model is implemented today,
  including item-aligned resume.

- [arch-testing](src/metaproc/docs/arch-testing.md) (`metaproc help arch-testing`): the
  test tiers, when to use each, and per-adapter credential setup.

- [arch-file-io-utilities](src/metaproc/docs/arch-file-io-utilities.md)
  (`metaproc help arch-file-io`): the curated `metaproc.io` surface and
  frontmatter_format gotchas.

The Metabrowser integration is split between the external
[MetaBrowser architecture](https://github.com/jlevy/metabrowser/blob/main/docs/architecture.md)
and the Metaproc-owned [plugin](src/metaproc/metabrowser_plugin/README.md); RunPool’s
module-level notes live in [`runpool/README.md`](src/metaproc/runpool/README.md).

### Operator Runbooks

Step-by-step procedures for operating Metaproc:

- **[Credential Setup](src/metaproc/docs/credential-setup.runbook.md)**
  (`metaproc help credentials`): configuring credentials for the Claude Code, Codex,
  Gemini, pi, and GCP adapters.
- **[Cloud Dispatch](src/metaproc/docs/cloud-dispatch.runbook.md)**
  (`metaproc help cloud-dispatch`): preparing, submitting, monitoring, and recovering
  GCP Batch workloads.

### Reference Docs

- **[Conventions](src/metaproc/docs/conventions.md)** (`metaproc help conventions`):
  framework-level naming, structure, and file-format rules.
- **[Metaproc Artifact Catalog](src/metaproc/docs/artifact-catalog.md)**
  (`metaproc help artifacts`): every runtime artifact Metaproc writes or reads, with
  filename, format, schema, lifecycle, writers, and readers.
- **[Metaproc Execution Model](src/metaproc/docs/execution-model-design.md)**
  (`metaproc help execution-contracts`): the durable contracts under task-level
  scheduling and their rationale.
- **[Model Pricing Reference](src/metaproc/data/pricing.md)**: per-model token and cache
  pricing for every provider the framework touches.
- **[Process Framework Theory](src/metaproc/docs/process-framework-theory.md)**
  (`metaproc help framework`): background theory on the general execution model beneath
  any process framework, with the map of how Metaproc instantiates it and where it
  deviates.

Agents get the same routing automatically: `metaproc skill metaproc --install` writes a
portable [Agent Skill](https://agentskills.io/specification) into
`.agents/skills/metaproc/` and `.claude/skills/metaproc/` that delegates to these
manuals.

Release history is separate from all of the above: see [CHANGELOG.md](CHANGELOG.md).

## Commands

The CLI is organized into a few families; `metaproc --help` lists every command,
`metaproc <command> --help` documents each one, and `metaproc help operator` maps
monitoring questions to commands.

| Family | Representative commands | Purpose |
| --- | --- | --- |
| Run | `run-process`, `run-step`, `plan`, `deps`, `validate`, `override`, `kill` | Plan and walk process DAGs, execute or acknowledge single steps, unblock or stop runs |
| Monitor | `status`, `wait`, `tail`, `pulse`, `stats`, `trace`, `resource-report`, `write-usage` | Run completion, health, logs, timing, cost, and resource reporting |
| Artifacts | `softschema`, `structure-report`, `check-headers`, `compact-logs`, `gzip-text` | Schema inspection and validation, frontmatter checks, log compaction |
| Credentials | `auth-check`, `auth push | list |
| Pools | `pool status | events |
| Cloud | `gcp run | status |
| Self-docs | `help`, `skill`, `env --template` | Bundled manuals, Agent Skill generation, environment template |

For an application process, the supported cloud entry point is currently
`metaproc run-process <spec> --backend gcp-worker --cloud`. It submits the process
orchestrator and its fan-out workers to GCP and preserves the process graph, resume
state, leases, claims, and monitoring contracts.
`metaproc gcp run` is a lower-level primitive for one command in one Batch task, such as
a probe, diagnostic, publisher, or an application that already owns its outer
orchestration. It is not a second process-orchestration API; do not build a process by
chaining `gcp run` calls.

The current `--backend` and `--cloud` spelling reflects the implemented CLI. The cloud
architecture documents the planned provider-neutral `--orchestrator`/`--worker`
placement model; those flags are not available yet.

## Process Specs

Process specs define multi-step DAGs that `run-process` walks automatically.
The repository ships provider-agnostic and per-adapter self-test processes:

| Process | Location | Purpose |
| --- | --- | --- |
| self-test/smoke-core | [process/self-test/smoke-core.process.md](process/self-test/smoke-core.process.md) | Provider-agnostic smoke: standalone lint, type, documentation, policy, and test gates |
| self-test/smoke-adapter-claude | [process/self-test/smoke-adapter-claude.process.md](process/self-test/smoke-adapter-claude.process.md) | Claude adapter: binary, credential, and live prompt |
| self-test/smoke-adapter-codex | [process/self-test/smoke-adapter-codex.process.md](process/self-test/smoke-adapter-codex.process.md) | Codex adapter: binary, credential, and live prompt |
| self-test/smoke-adapter-gemini | [process/self-test/smoke-adapter-gemini.process.md](process/self-test/smoke-adapter-gemini.process.md) | Gemini adapter: binary, credential, and live prompt |
| self-test/smoke-adapter-pi | [process/self-test/smoke-adapter-pi.process.md](process/self-test/smoke-adapter-pi.process.md) | pi adapter: binary, credential, and live prompt (Vertex MaaS) |

See [testing architecture](src/metaproc/docs/arch-testing.md) for when to use each tier
and how to set up per-adapter credentials.
Downstream packages own their domain process specs, schemas, handlers, fixtures, and
runbooks.

## Working with a Coding Agent

This repository includes [`AGENTS.md`](AGENTS.md), with the build, test, dependency,
layout, and release conventions a coding agent needs for routine work.
`CLAUDE.md` imports the same instructions for Claude Code.

For an ordinary change, tell your agent: “Read `AGENTS.md`, implement this change, and
run the required checks.”
For toolchain or template maintenance, ask it to use the
[simple-modern-uv skill](https://github.com/jlevy/simple-modern-uv/tree/main/skills/simple-modern-uv),
which distinguishes selective feature adoption from a full Copier update.

## Development

The repository follows the `simple-modern-uv` structure and uses uv, Ruff, BasedPyright,
pytest, Biome, TypeScript, Flowmark, and Lefthook:

```shell
make install
make format
make verify
```

`make verify` checks both locks, formatting, Python and browser lint, types, tests,
dependency audits, public hygiene, source and wheel contents, and an isolated installed
wheel. The full index of contributor documentation is [Project Docs](#project-docs),
directly below.

## Project Docs

Everything under [Documentation](#documentation) above describes the framework and ships
in the package. The documents below are for developers and agents working on Metaproc
itself; they live only in the repository:

- [development](docs/development.md): the dev guide, covering layout, conventions,
  testing, and how the shipped documentation set is maintained.
- [installation](docs/installation.md): install paths for uvx, uv tool, a source
  checkout, and the Agent Skill.
- [AGENTS.md](AGENTS.md): instructions for coding agents working in this repository.
- [agent-toolchain-bootstrap](docs/agent-toolchain-bootstrap.md): the toolchain pins
  agent sessions install for themselves, where that is wired, and how it is guarded.
- [SUPPLY-CHAIN-SECURITY](SUPPLY-CHAIN-SECURITY.md): dependency policy, including the
  cool-off, lockfiles, and audited exceptions.
- [SECURITY](SECURITY.md): vulnerability reporting and security boundaries.
- [publishing](docs/publishing.md): the release process, with PyPI trusted publishing.
- [performance-notes](docs/performance-notes.md): performance principles, tooling, and
  worked examples.
- [memory-accounting-reference](docs/memory-accounting-reference.md): which memory
  counters mean what on macOS and Linux; the background behind how RunPool sizes
  concurrency.
- [TODO](TODO.md): the current release and deferred quality work.

Repository runbooks, in [`docs/runbooks/`](docs/runbooks/):

- [environment-bootstrap](docs/runbooks/environment-bootstrap.runbook.md): end-to-end
  setup for running workflows, from locks and the offline smoke to adapters and GCP
  preflight.
- [adapter-compatibility](docs/runbooks/adapter-compatibility.runbook.md):
  provider-routing nuances such as API paths, variants, and tool-use attribution.
- [adding-a-new-llm-provider](docs/runbooks/adding-a-new-llm-provider.runbook.md):
  provider onboarding across the registry, catalog, pricing, secrets, and smoke tests.
- [softschema-validation](docs/runbooks/softschema-validation.runbook.md): validating
  softschema-tagged artifacts.
- [browser-streaming-smoke](docs/runbooks/browser-streaming-smoke.runbook.md): the
  manual Metabrowser UI verification checklist.

Project records, the material explaining how the project got where it is, live in
[`docs/project/`](docs/project/README.md): implementation plans under
[specs](docs/project/specs/), design records, revision histories, and per-document
backlogs under [design](docs/project/design/), long-form release notes under
[releases](docs/project/releases/), and the extraction record under
[provenance](docs/project/provenance/).

## Compatibility

During the 0.x series, the command-line interface, process-spec format, documented
plugin entry points, and Pydantic models explicitly linked from the architecture docs
are the supported integration surfaces.
Other Python imports are implementation details and may change between minor releases.

Cloud images do not pick up local source edits automatically; publish or upload a wheel
and set both `METAPROC_WHEEL_GCS` and `METAPROC_WHEEL_SHA256`, or rebuild the downstream
image. See [cloud-dispatch](src/metaproc/docs/cloud-dispatch.runbook.md).

## License

Metaproc is AGPL-3.0-or-later; see [LICENSE](LICENSE). If you modify Metaproc and let
users interact with that modified version over a network, AGPL section 13 requires
offering those users the corresponding source code for the running version.

The vendored ELK browser component is a separately licensed work; its license and
distribution notice are listed in [NOTICE.md](NOTICE.md).

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
