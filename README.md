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
explicitly.
Template variables are case-sensitive; see [conventions](docs/conventions.md)
for the casing rules and the small set of framework built-ins.

## Documentation

Metaproc documents itself: the manuals below ship inside the package, and everything
else lives in [`docs/`](docs/).

### Start Here

| Document | Purpose |
| --- | --- |
| [installation](docs/installation.md) | Install paths: uvx, uv tool, source checkout, Agent Skill |
| `metaproc help concepts` | The conceptual model: vocabulary, planes, step modes, optimization loops ([source](src/metaproc/docs/metaproc-concepts-and-principles.md)) |
| `metaproc help operator` | Runtime reference: starting, monitoring, resuming, and stopping runs ([source](src/metaproc/docs/metaproc-operator-reference.md)) |
| `metaproc help developer` | Extending metaproc and the “metaproc is the right wrapper” policy ([source](src/metaproc/docs/metaproc-developer-guide.md)) |

Agents get the same routing automatically: `metaproc skill metaproc --install` writes a
portable [Agent Skill](https://agentskills.io/specification) into
`.agents/skills/metaproc/` and `.claude/skills/metaproc/` that delegates to these
manuals.

### Reference

| Document | Purpose |
| --- | --- |
| [conventions](docs/conventions.md) | Framework-level naming, structure, and file-format rules (see §File Format Policy) |
| [artifact-catalog](docs/artifact-catalog.md) | Every runtime artifact Metaproc writes or reads: filename, format, schema, lifecycle, writers, and readers |
| [pricing](src/metaproc/data/pricing.md) | Per-model token and cache pricing for every provider the framework touches; drives cost-per-record math |
| [CHANGELOG](CHANGELOG.md) | Release history and upgrade notes |

### Runbooks

Operational procedures live in [`docs/runbooks/`](docs/runbooks/):

| Runbook | Purpose |
| --- | --- |
| [environment-bootstrap](docs/runbooks/environment-bootstrap.runbook.md) | End-to-end setup for running workflows: locks, offline smoke, adapters, GCP preflight |
| [credential-setup](docs/runbooks/credential-setup.runbook.md) | Adapter credential configuration for Claude Code, Codex, Gemini, pi, and GCP |
| [cloud-dispatch](docs/runbooks/cloud-dispatch.runbook.md) | Preparing, submitting, monitoring, and recovering GCP Batch workloads |
| [adapter-compatibility](docs/runbooks/adapter-compatibility.runbook.md) | Provider-routing nuances: API paths, variants, tool-use attribution |
| [adding-a-new-llm-provider](docs/runbooks/adding-a-new-llm-provider.runbook.md) | Provider onboarding: registry, catalog, pricing, secrets, smoke tests |
| [softschema-validation](docs/runbooks/softschema-validation.runbook.md) | Validating softschema-tagged artifacts |
| [browser-streaming-smoke](docs/runbooks/browser-streaming-smoke.runbook.md) | Manual Metabrowser UI verification checklist |
| [claude-code-cli-remote-vm](docs/runbooks/claude-code-cli-remote-vm.runbook.md) | Superseded per-developer VM path for the Claude Code adapter |

### Architecture

Architecture docs live in [`docs/arch/`](docs/arch/); the maintained index with status
and ownership is
[development.md § Architecture Docs](docs/development.md#architecture-docs).
[arch-metaproc-core](docs/arch/arch-metaproc-core.md) is the primary implementation
reference; [metaproc-design-rev3-proposals](docs/metaproc-design-rev3-proposals.md)
holds design proposals not yet implemented.
The Metabrowser integration is split between the external
[MetaBrowser architecture](https://github.com/jlevy/metabrowser/blob/main/docs/architecture.md)
and the Metaproc-owned [plugin](src/metaproc/metabrowser_plugin/README.md).

### Contributing and Policies

| Document | Purpose |
| --- | --- |
| [development](docs/development.md) | Dev guide for hacking on metaproc itself: layout, conventions, testing, arch-doc index |
| [AGENTS.md](AGENTS.md) | Instructions for coding agents working in this repository |
| [SUPPLY-CHAIN-SECURITY](SUPPLY-CHAIN-SECURITY.md) | Dependency policy: cool-off, lockfiles, audited exceptions |
| [SECURITY](SECURITY.md) | Vulnerability reporting and security boundaries |
| [publishing](docs/publishing.md) | Release process with PyPI trusted publishing |
| [performance-notes](docs/performance-notes.md) | Performance principles, tooling, and worked examples |
| [project records](docs/project/README.md) | Active and completed implementation plans plus extraction provenance |
| [TODO](TODO.md) | Current release and deferred quality work |

## Commands

The CLI is organized into a few families; `metaproc --help` lists every command,
`metaproc <command> --help` documents each one, and `metaproc help operator` maps
monitoring questions to commands.

| Family | Representative commands | Purpose |
| --- | --- | --- |
| Run | `run-process`, `run-step`, `plan`, `deps`, `validate`, `override`, `kill` | Plan and walk process DAGs, execute or acknowledge single steps, unblock or stop runs |
| Monitor | `status`, `wait`, `tail`, `pulse`, `stats`, `trace`, `resource-report`, `write-usage` | Run completion, health, logs, timing, cost, and resource reporting |
| Artifacts | `softschema`, `structure-report`, `check-headers`, `compact-logs`, `gzip-text` | Schema inspection and validation, frontmatter checks, log compaction |
| Credentials | `auth-check`, `auth push/list/probe/status/enable/disable/rotate/prune` | Operator preflight and labeled credential-pool lifecycle |
| Pools | `pool status/events/concurrency-timeline/rollup/retry-missing` | RunPool snapshots, event logs, concurrency history, rollups |
| Cloud | `gcp status/scale/logs/cancel/runs/resources/archive/remote/cleanup` | GCP Batch dispatch monitoring and lifecycle (optional extras) |
| Self-docs | `help`, `skill`, `env --template` | Bundled manuals, Agent Skill generation, environment template |

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

See [testing architecture](docs/arch/arch-testing.md) for when to use each tier and how
to set up per-adapter credentials.
Downstream packages own their domain process specs, schemas, handlers, fixtures, and
runbooks.

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
wheel. See [development](docs/development.md), [agent instructions](AGENTS.md), and
[supply-chain security](SUPPLY-CHAIN-SECURITY.md).

## Compatibility

During the 0.x series, the command-line interface, process-spec format, documented
plugin entry points, and Pydantic models explicitly linked from the architecture docs
are the supported integration surfaces.
Other Python imports are implementation details and may change between minor releases.

Cloud images do not pick up local source edits automatically; publish or upload a wheel
and set both `METAPROC_WHEEL_GCS` and `METAPROC_WHEEL_SHA256`, or rebuild the downstream
image. See [cloud-dispatch](docs/runbooks/cloud-dispatch.runbook.md).

## License

Metaproc is AGPL-3.0-or-later; see [LICENSE](LICENSE). If you modify Metaproc and let
users interact with that modified version over a network, AGPL section 13 requires
offering those users the corresponding source code for the running version.

The vendored ELK browser component is a separately licensed work; its license and
distribution notice are listed in [NOTICE.md](NOTICE.md).

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
