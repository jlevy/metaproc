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
- RunPool concurrency, retry, resource-pressure, credential-pool, and observability
  controls
- Optional GCP Batch dispatch and Secret Manager integration
- A packaged Metabrowser plugin for process specs, plans, traces, logs, and resource
  reports
- A generated portable Agent Skill installed by the `metaproc skill` command

## Installation

Metaproc requires Python 3.12 or newer and uses [uv](https://docs.astral.sh/uv/). After
the first public release, run a pinned version without a persistent installation:

```shell
uvx metaproc@0.1.0 --help
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

See [installation](docs/installation.md) for source-checkout and upgrade instructions.

## Entry Points

| Document | Purpose |
| --- | --- |
| [docs/arch/arch-metaproc-core.md](docs/arch/arch-metaproc-core.md) | Current framework architecture and implementation reference |
| [metaproc-operator-reference.md](src/metaproc/docs/metaproc-operator-reference.md) | Operator command map and current `.state` / `.logs` runtime artifact reference |
| [docs/arch/arch-metaproc-core.md §14.7](docs/arch/arch-metaproc-core.md#147-tool-use-observability) | Tool-use observability — data-source triad, aggregation contract, failure taxonomy, cutoff-discipline and native web-search invariants |
| [docs/arch/arch-cloud-execution.md](docs/arch/arch-cloud-execution.md) | GCP Batch dispatch shape (containers, `compute_resource`, service accounts, secrets) |
| [docs/arch/arch-runpool.md](docs/arch/arch-runpool.md) | RunPool subsystem boundary, adaptive resource control, and module-local design docs |
| [docs/metaproc-design-rev3-proposals.md](docs/metaproc-design-rev3-proposals.md) | Remaining design proposals not yet implemented on this branch |
| [docs/conventions.md](docs/conventions.md) | Framework-level naming, structure, and file-format rules (see §File Format Policy) |
| [docs/artifact-catalog.md](docs/artifact-catalog.md) | Every runtime artifact metaproc writes or reads — filename, format, schema, lifecycle, writer/readers |
| [docs/runbooks/credential-setup.runbook.md](docs/runbooks/credential-setup.runbook.md) | Adapter credential configuration |
| [docs/arch/arch-authentication.md](docs/arch/arch-authentication.md) | Credential pool design (labels, fallback policy, probes, rotation) |
| [docs/runbooks/adding-a-new-llm-provider.runbook.md](docs/runbooks/adding-a-new-llm-provider.runbook.md) | Provider onboarding runbook (adapter wiring, auth registration, smoke tests) |
| [docs/development.md](docs/development.md) | **Concise dev guide for hacking on metaproc itself** — code layout, conventions, testing |
| [docs/performance-notes.md](docs/performance-notes.md) | Historical measurements and performance principles for the external browser integration |
| [docs/runbooks/environment-bootstrap.runbook.md](docs/runbooks/environment-bootstrap.runbook.md) | End-to-end setup for running workflows (gcloud, agent CLIs, and preflight) |
| [docs/metaproc-operator-reference.md § Domain Dispatch Pattern](src/metaproc/docs/metaproc-operator-reference.md#domain-dispatch-pattern) | How client workflows keep roster/tier/source-health policy outside metaproc while using the standard status, pool, trace, and kill commands |
| [MetaBrowser architecture](https://github.com/jlevy/metabrowser/blob/main/docs/architecture.md) | Standalone browser architecture and package boundary |
| [Metaproc MetaBrowser plugin](src/metaproc/metabrowser_plugin/README.md) | Metaproc-owned file kinds, visualizations, log adapters, data hooks, and plugin validation |
| [docs/arch/arch-testing.md](docs/arch/arch-testing.md) | Smoke / unit / integration testing tiers and commands |
| [src/metaproc/data/pricing.md](src/metaproc/data/pricing.md) | **Per-model token / cache pricing** for every provider + model the framework touches. Drives cost-per-record math; update whenever a provider publishes new rate cards or when a new model lands in `pi-models.default.json`. |
| [CHANGELOG.md](CHANGELOG.md) | Release history and upgrade notes |
| [TODO.md](TODO.md) | Current focus, active-spec status, and pointers to blocking beads |

## Usage

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

Client process specs may require additional env vars before execution.
The most common one is `RUNS_DIR`: an absolute path used to template output locations
via `{{run.parent_dir}}`. Workflow launchers are responsible for resolving `RUNS_DIR`
from whatever domain-specific settings they own and passing the absolute value into
`run-process` / `run-step` (either via `--var RUNS_DIR=...` or by setting it in the
environment). Metaproc itself stays workflow-agnostic and does not synthesize `RUNS_DIR`
from any domain-specific source.

The [offline example](examples/offline-smoke/offline-smoke.process.md) passes the value
explicitly.

Template variables are case-sensitive.
Framework built-ins are intentionally small: `run.parent_dir`, `run.id`,
`run.execution_profile`, `run.artifact_namespace`, `run.variant`, `step.prompt_path`,
and `step.outputs_list`. `run.variant` is a migration alias for
`run.artifact_namespace`. Names like `RUN_ID` or `DATE` are ordinary process params
unless the process spec says otherwise; fan-out fields preserve the exact
authored/source key names.
See [docs/conventions.md](docs/conventions.md) for the full casing rules.

## Key Commands

| Command | Purpose |
| --- | --- |
| `run-process` | Walk a full process DAG — the primary user-facing command |
| `run-step` | Execute one step directly or acknowledge a `mode:manual` gate |
| `run-parallel` | Low-level fan-out plumbing (used internally by worker VMs) |
| `plan` | Resolve and print the current execution plan |
| `deps` | Show declared deps with inferred runtime state for a planned run |
| `validate` | Check expected outputs for a specific completed step |
| `softschema inspect` | Inspect a frontmatter artifact’s declared schema, binding, status, stage, and envelope |
| `softschema validate --schema <id>` | Validate one artifact through the registered softschema binding |
| `softschema compile --check` | Compile or drift-check a Pydantic model’s YAML schema sidecar |
| `structure-report` | Generate a process boundary map with schema/status/stage/profile summaries |
| `check-headers` | Walk the process tree and validate frontmatter |
| `status` | Check run completion and progress |
| `wait` | Block until a run reaches terminal state |
| `tail` | Tail JSONL log files from `.logs/` directories |
| `auth-check` | Operator preflight: verify credentials and API connectivity for the active dispatch |
| `auth push` | Push a CLI adapter credential into the labeled pool (Secret Manager or local FS) |
| `auth push --probe` | Push then run a real-API probe in one operator action |
| `auth list` | List pool entries — labels + state, never the payload |
| `auth probe` | Real-API probe of a pool credential; updates pool state with retry-tolerant CAS |
| `auth status` | At-a-glance pool readiness — which labels work, which are throttled |
| `auth enable` / `disable` / `rotate` / `prune` | Lifecycle operations on pool entries |
| `run-process --auth-account / --auth-include-labels / --auth-exclude-labels / --auth-fallback-policy` | Wire fan-out steps to the credential pool with explicit label scoping |
| `compact-logs` | Compact adapter JSONL logs for long-term storage/debugging |
| `gzip-text` | Gzip large text artifacts and logs with byte-fidelity verification |
| `override` | Mark a step as satisfied so downstream resumes when partial-fail blocks the DAG (audit trail in `.state/overrides.yaml`) |
| `kill` | Terminate a running pool and its child processes |
| `write-usage` | Roll up token and cost usage into `usage.md` |
| `resource-report` | Build or refresh the hierarchical `resources.json` run report |
| `stats` | Summarize throughput, timing, pool, API, and resource usage |
| `gcp status` | Show GCP Batch job status for a run |
| `gcp scale` | Update desired worker topology for an active cloud fan-out step |
| `gcp logs` | Stream Cloud Logging for a run’s GCP Batch jobs |
| `gcp cancel` | Cancel running/queued Batch jobs for a run |
| `gcp runs` | List all active metaproc runs across the GCP project |
| `gcp resources` | Snapshot metaproc-related GCP assets via Cloud Asset Inventory |
| `gcp filestore` | Inspect Filestore instance status and utilization |
| `gcp archive` | Sync completed runs to GCS for long-term retention |
| `gcp remote` | Run a metaproc command on the remote gateway host via SSH/IAP |
| `gcp remote-run` | Launch run-process in a tmux session on a remote host (survives disconnects) |
| `gcp cleanup` | Delete old terminal-state GCP Batch jobs |
| `pool status` | Inspect RunPool status snapshots |
| `pool events` | Inspect RunPool event logs |
| `pool events --summary` | One-line-per-event summary view of the pool event log |
| `pool concurrency-timeline` | Show how the pool’s concurrency cap evolved over a run |
| `pool rollup` | Roll up pool status across every sub-step pool in a run |
| `pool rollup --auth-outcomes` | Roll up + per-label `auth_outcome` event stats (success / cooling / expired) |
| `pool retry-missing` | Reset completed markers when cloud outputs are missing |

## Process Specs

Process specs define multi-step DAGs that `run-process` walks automatically.

| Process | Location | Purpose |
| --- | --- | --- |
| self-test/smoke-core | [process/self-test/smoke-core.process.md](process/self-test/smoke-core.process.md) | Provider-agnostic smoke: standalone lint, type, documentation, policy, and test gates |
| self-test/smoke-adapter-claude | [process/self-test/smoke-adapter-claude.process.md](process/self-test/smoke-adapter-claude.process.md) | Claude adapter: binary + credential + live prompt |
| self-test/smoke-adapter-codex | [process/self-test/smoke-adapter-codex.process.md](process/self-test/smoke-adapter-codex.process.md) | Codex adapter: binary + credential + live prompt |
| self-test/smoke-adapter-gemini | [process/self-test/smoke-adapter-gemini.process.md](process/self-test/smoke-adapter-gemini.process.md) | Gemini adapter: binary + credential + live prompt |
| self-test/smoke-adapter-pi | [process/self-test/smoke-adapter-pi.process.md](process/self-test/smoke-adapter-pi.process.md) | pi adapter: binary + credential + live prompt (Vertex MaaS) |

See [testing architecture](docs/arch/arch-testing.md) for when to use each tier and how
to set up per-adapter credentials.
Downstream packages own their domain process specs, schemas, handlers, fixtures, and
runbooks.

## Notes

- For a fresh checkout, use `make install`; it installs the exact committed Python and
  JavaScript locks.
- [arch-metaproc-core.md](docs/arch/arch-metaproc-core.md) describes the current
  implementation.
- Remaining not-yet-implemented rev3 work lives in
  [`docs/metaproc-design-rev3-proposals.md`](docs/metaproc-design-rev3-proposals.md); it
  remains separate from the runtime contract.
- Cloud images do not pick up local source edits automatically.
  Publish or upload a wheel and set `METAPROC_WHEEL_GCS`, or rebuild the downstream
  image.

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
wheel. See [agent instructions](AGENTS.md), [development](docs/development.md), and
[supply-chain security](SUPPLY-CHAIN-SECURITY.md).

## Compatibility

During the 0.x series, the command-line interface, process-spec format, documented
plugin entry points, and Pydantic models explicitly linked from the architecture docs
are the supported integration surfaces.
Other Python imports are implementation details and may change between minor releases.

## License

Metaproc is AGPL-3.0-or-later; see [LICENSE](LICENSE). If you modify Metaproc and let
users interact with that modified version over a network, AGPL section 13 requires
offering those users the corresponding source code for the running version.

The vendored ELK browser component is a separately licensed work; its license and
distribution notice are listed in [NOTICE.md](NOTICE.md).

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
